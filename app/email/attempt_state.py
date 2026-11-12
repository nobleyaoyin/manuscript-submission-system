"""Explicit fake-only outbox attempt simulation; never sends email.

The persistence functions use caller-owned transactions. The simulation owns
and commits its short transactions, and never invokes a real email transport.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

from app.email.worker_store import Lease, _begin, _timestamp


class Outcome(str, Enum):
    ACCEPTED = 'accepted'
    RETRYABLE = 'retryable'
    PERMANENT = 'permanent'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    outbox_id: str
    number: int
    lease_token: str


class FakeTransport:
    """Deterministic synthetic outcomes only; no message, network, or disk."""
    def __init__(self, outcome):
        if not isinstance(outcome, Outcome):
            raise ValueError('A synthetic outcome is required')
        self.outcome = outcome
        self.calls = 0

    def simulate(self):
        self.calls += 1
        return self.outcome


def _stamp(now):
    return _timestamp(datetime.now(timezone.utc) if now is None else now)


def _limit(max_attempts):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
        raise ValueError('Attempt limit must be an integer between 1 and 100')


def start_attempt(db, lease, *, max_attempts, now=None):
    """Start one attempt under a current lease; caller commits or rolls back."""
    if not isinstance(lease, Lease):
        raise TypeError('Lease is required')
    _limit(max_attempts)
    stamp = _stamp(now)
    _begin(db)
    row = db.execute('''SELECT o.attempt_count, COALESCE(MAX(a.attempt_number), 0),
        COALESCE(SUM(CASE WHEN a.outcome <> 'retryable' THEN 1 ELSE 0 END), 0)
        FROM email_outbox o LEFT JOIN email_delivery_attempts a ON a.outbox_id=o.outbox_id
        WHERE o.outbox_id=? AND o.status='processing' AND o.lease_token=? AND o.lease_until>?
        GROUP BY o.outbox_id''', (lease.outbox_id, lease.token, stamp)).fetchone()
    if row is None or row[0] != row[1] or row[2] or row[0] >= max_attempts:
        raise ValueError('Lease or attempt ledger is not eligible')
    number = row[0] + 1
    changed = db.execute('''UPDATE email_outbox SET attempt_count=?, updated_at=?
        WHERE outbox_id=? AND status='processing' AND lease_token=? AND lease_until>?
        AND attempt_count=?''', (number, stamp, lease.outbox_id, lease.token, stamp, row[0]))
    if changed.rowcount != 1:
        raise RuntimeError('Attempt claim changed unexpectedly')
    attempt = Attempt(str(uuid4()), lease.outbox_id, number, lease.token)
    db.execute('''INSERT INTO email_delivery_attempts
        (attempt_id,outbox_id,attempt_number,lease_token,started_at,outcome)
        VALUES (?,?,?,?,?,'started')''',
        (attempt.attempt_id, attempt.outbox_id, number, lease.token, stamp))
    return attempt


def finish_attempt(db, lease, attempt, outcome, *, max_attempts, retry_delay, now=None):
    """Record a synthetic outcome atomically under the same unexpired lease."""
    if not isinstance(lease, Lease) or not isinstance(attempt, Attempt):
        raise TypeError('Lease and attempt are required')
    if not isinstance(outcome, Outcome):
        raise ValueError('Invalid synthetic outcome')
    if attempt.outbox_id != lease.outbox_id or attempt.lease_token != lease.token:
        raise ValueError('Attempt does not match lease')
    _limit(max_attempts)
    if not isinstance(retry_delay, timedelta) or not timedelta(0) < retry_delay <= timedelta(days=1):
        raise ValueError('Retry delay must be positive and no more than one day')
    current = datetime.now(timezone.utc) if now is None else now
    stamp = _timestamp(current)
    due = _timestamp(current + retry_delay)
    _begin(db)
    if outcome is Outcome.ACCEPTED:
        status, due, accepted, reason = 'accepted', None, stamp, None
    elif outcome is Outcome.RETRYABLE and attempt.number < max_attempts:
        status, accepted, reason = 'retry_wait', None, None
    elif outcome is Outcome.RETRYABLE:
        status, due, accepted, reason = 'failed', None, None, 'retry_limit'
    elif outcome is Outcome.PERMANENT:
        status, due, accepted, reason = 'failed', None, None, 'permanent'
    else:
        status, due, accepted, reason = 'unknown', None, None, 'ambiguous'
    updated = db.execute('''UPDATE email_delivery_attempts SET outcome=?, finished_at=?
        WHERE attempt_id=? AND outbox_id=? AND attempt_number=? AND lease_token=? AND outcome='started'
        AND EXISTS (SELECT 1 FROM email_outbox WHERE outbox_id=? AND status='processing'
            AND lease_token=? AND lease_until>? AND attempt_count=?)''',
        (outcome.value, stamp, attempt.attempt_id, lease.outbox_id, attempt.number,
         lease.token, lease.outbox_id, lease.token, stamp, attempt.number))
    if updated.rowcount != 1:
        raise ValueError('Lease or attempt is no longer current')
    updated = db.execute('''UPDATE email_outbox SET status=?,next_attempt_at=?,accepted_at=?,
        lease_token=NULL,lease_until=NULL,updated_at=?,terminal_reason=?
        WHERE outbox_id=? AND status='processing' AND lease_token=? AND lease_until>?
        AND attempt_count=?''',
        (status, due, accepted, stamp, reason, lease.outbox_id, lease.token, stamp, attempt.number))
    if updated.rowcount != 1:
        raise RuntimeError('Attempt state changed unexpectedly')
    return status


def simulate_attempt(db, lease, fake, *, max_attempts, retry_delay, clock=None):
    """Persist start, commit, then invoke an exclusively in-memory fake.

    Any exception or ambiguous start commit prevents the simulation. A failure
    after it starts leaves a durable started row for conservative recovery.
    """
    if type(fake) is not FakeTransport:
        raise TypeError('Only the built-in fake transport is supported')
    if db.in_transaction:
        raise ValueError('A fresh connection transaction is required')
    clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        attempt = start_attempt(db, lease, max_attempts=max_attempts, now=clock())
        db.commit()
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    # No write transaction is held during the in-memory simulation.
    outcome = fake.simulate()
    try:
        status = finish_attempt(db, lease, attempt, outcome, max_attempts=max_attempts,
                                retry_delay=retry_delay, now=clock())
        db.commit()
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    return status
