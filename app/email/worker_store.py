"""Explicit, non-network SQLite claim/lease primitives for migration 007.

Pass a dedicated sqlite3 connection with foreign keys enabled and a busy timeout
of at most one second. Each operation starts BEGIN IMMEDIATE and leaves the
transaction to its caller: commit before using a lease, otherwise roll back.
Never treat an ambiguous commit as a confirmed claim.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
import sqlite3


@dataclass(frozen=True)
class Lease:
    outbox_id: str
    token: str
    expires_at: str


@dataclass(frozen=True)
class Recovery:
    outbox_id: str
    status: str


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('An aware datetime is required')
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _times(now, duration):
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(duration, timedelta) or duration <= timedelta(0):
        raise ValueError('Lease duration must be positive')
    start = _timestamp(now)
    end = _timestamp(now + duration)
    if end <= start:
        raise ValueError('Lease expiration must follow the current time')
    return start, end


def _begin(db):
    if not isinstance(db, sqlite3.Connection):
        raise TypeError('A dedicated sqlite3 connection is required')
    if db.in_transaction:
        raise ValueError('A fresh connection transaction is required')
    if db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('SQLite foreign keys must be enabled')
    if db.execute('PRAGMA busy_timeout').fetchone()[0] > 1000:
        raise ValueError('SQLite busy timeout must not exceed 1000 milliseconds')
    # Obtain the write lock before reading a candidate. OperationalError on
    # contention propagates; there is no retry or transaction replay here.
    db.execute('BEGIN IMMEDIATE')


def claim_next(db, *, lease_duration, now=None):
    """Claim one due intent, or return None; caller owns commit/rollback.

    Only rows with no unresolved, accepted or permanent attempt are eligible.
    This defensive ledger check also prevents inconsistent legacy states from
    being silently resent. Business/recipient eligibility is future work.
    """
    stamp, until = _times(now, lease_duration)
    token = secrets.token_urlsafe(32)
    _begin(db)
    row = db.execute("""
        SELECT o.outbox_id FROM email_outbox AS o
        WHERE o.status IN ('pending', 'retry_wait') AND o.next_attempt_at <= ?
          AND NOT EXISTS (SELECT 1 FROM email_delivery_attempts AS a
              WHERE a.outbox_id = o.outbox_id AND a.outcome <> 'retryable')
        ORDER BY o.next_attempt_at, o.created_at, o.outbox_id LIMIT 1
    """, (stamp,)).fetchone()
    if row is None:
        return None
    result = db.execute("""
        UPDATE email_outbox SET status='processing', next_attempt_at=NULL,
            lease_token=?, lease_until=?, updated_at=?
        WHERE outbox_id=? AND status IN ('pending', 'retry_wait')
          AND next_attempt_at <= ?
          AND NOT EXISTS (SELECT 1 FROM email_delivery_attempts AS a
              WHERE a.outbox_id=email_outbox.outbox_id AND a.outcome <> 'retryable')
    """, (token, until, stamp, row[0], stamp))
    if result.rowcount != 1:
        raise RuntimeError('Outbox claim changed unexpectedly')
    return Lease(row[0], token, until)


def renew_lease(db, lease, *, lease_duration, now=None):
    """Extend a current unexpired lease; stale tokens cannot update a row."""
    if not isinstance(lease, Lease):
        raise TypeError('Lease is required')
    stamp, until = _times(now, lease_duration)
    _begin(db)
    result = db.execute("""
        UPDATE email_outbox SET lease_until=?, updated_at=?
        WHERE outbox_id=? AND status='processing' AND lease_token=?
          AND lease_until > ? AND lease_until < ?
    """, (until, stamp, lease.outbox_id, lease.token, stamp, until))
    return Lease(lease.outbox_id, lease.token, until) if result.rowcount == 1 else None


def recover_expired(db, *, now=None):
    """Recover one expired lease, conservatively classifying unsafe attempts.

    A ledger with no attempt except completed retryable failures can return to
    pending. Any other outcome, including started, is unknown; started attempts
    are finalized as unknown in the same caller-owned transaction.
    """
    stamp = _timestamp(datetime.now(timezone.utc) if now is None else now)
    _begin(db)
    row = db.execute("""
        SELECT outbox_id, lease_token FROM email_outbox
        WHERE status='processing' AND lease_until <= ?
        ORDER BY lease_until, outbox_id LIMIT 1
    """, (stamp,)).fetchone()
    if row is None:
        return None
    unsafe = db.execute("""
        SELECT 1 FROM email_delivery_attempts
        WHERE outbox_id=? AND outcome <> 'retryable' LIMIT 1
    """, (row[0],)).fetchone() is not None
    status = 'unknown' if unsafe else 'pending'
    result = db.execute("""
        UPDATE email_outbox SET status=?, next_attempt_at=?, lease_token=NULL,
            lease_until=NULL, updated_at=?, terminal_reason=?
        WHERE outbox_id=? AND status='processing' AND lease_token=? AND lease_until <= ?
    """, (status, None if unsafe else stamp, stamp,
          'expired_with_attempt' if unsafe else None, row[0], row[1], stamp))
    if result.rowcount != 1:
        raise RuntimeError('Outbox recovery changed unexpectedly')
    if unsafe:
        db.execute("""
            UPDATE email_delivery_attempts SET outcome='unknown', finished_at=?,
                error_code='lease_expired'
            WHERE outbox_id=? AND outcome='started'
        """, (stamp, row[0]))
    return Recovery(row[0], status)
