"""Explicit SQLite checks for prospective reviewer-invitation delivery.

No email is sent. A positive result is a transaction-local observation, not
permission to deliver later without another check and an approved send design.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email_validator import EmailNotValidError, validate_email

from app.email.attempt_state import start_attempt
from app.email.worker_store import Lease, _begin, _timestamp


class InvitationIneligible(ValueError):
    """Sanitized fail-closed result; never contains email or manuscript data."""


@dataclass(frozen=True)
class Eligibility:
    outbox_id: str
    assignment_id: int
    recipient_user_id: int
    review_round: int


def _check_locked(db, lease, stamp):
    # LEFT JOIN makes absent/corrupt records fail closed without mistaking a
    # missing verification for a verified legacy account.
    row = db.execute('''SELECT o.event_id,o.recipient_user_id,o.template_key,o.template_version,
        e.event_type,e.schema_version,e.invitation_assignment_id,
        a.assignment_id,a.reviewer_id,a.status,a.review_round,a.manuscript_id,
        i.assignment_id,i.status,i.responded_at,m.manuscript_id,m.status,m.review_round,
        u.user_id,u.role,u.email,v.user_id,v.email,v.verified_at,
        rf.assignment_id,r.round_file_id,r.manuscript_id,r.review_round,r.source_kind,r.confirmation_id
        FROM email_outbox o
        LEFT JOIN notification_events e ON e.event_id=o.event_id
        LEFT JOIN review_assignments a ON a.assignment_id=e.invitation_assignment_id
        LEFT JOIN reviewer_invitations i ON i.assignment_id=a.assignment_id
        LEFT JOIN manuscripts m ON m.manuscript_id=a.manuscript_id
        LEFT JOIN users u ON u.user_id=o.recipient_user_id
        LEFT JOIN user_email_verifications v ON v.user_id=u.user_id
        LEFT JOIN review_assignment_files rf ON rf.assignment_id=a.assignment_id
        LEFT JOIN review_round_files r ON r.round_file_id=rf.round_file_id
        WHERE o.outbox_id=? AND o.status='processing' AND o.lease_token=? AND o.lease_until>?''',
        (lease.outbox_id, lease.token, stamp)).fetchone()
    if row is None:
        raise InvitationIneligible('Lease or outbox unavailable')
    (event_id, recipient, template, version, event_type, schema, event_assignment,
     assignment, reviewer, assignment_status, round_no, manuscript,
     invitation, invitation_status, responded_at, manuscript_id, manuscript_status, manuscript_round,
     user_id, role, email, verified_user, verified_email, verified_at,
     binding, round_file, file_manuscript, file_round, source_kind, confirmation) = row
    if (not event_id or event_type != 'reviewer.invitation.created' or schema != 1
            or template != 'reviewer_invitation' or version != 'v1'
            or assignment is None or event_assignment != assignment
            or reviewer != recipient or user_id != recipient or role != 'reviewer'):
        raise InvitationIneligible('Invitation identity unavailable or inconsistent')
    if (invitation != assignment or invitation_status != 'pending' or responded_at is not None
            or assignment_status != 'assigned' or manuscript_id != manuscript
            or manuscript_status not in ('ready_for_peer_review', 'revised')
            or type(round_no) is not int or round_no < 1 or manuscript_round != round_no):
        raise InvitationIneligible('Invitation is no longer pending for the current round')
    if (binding != assignment or round_file is None or file_manuscript != manuscript
            or file_round != round_no
            or (round_no == 1 and (source_kind != 'initial_editing' or confirmation is None))
            or (round_no > 1 and (source_kind != 'author_revision' or confirmation is not None))):
        raise InvitationIneligible('Review-round file binding unavailable or inconsistent')
    if (verified_user != recipient or not isinstance(email, str) or not email.strip()
            or not isinstance(verified_email, str) or not verified_at
            or verified_email != email.strip().lower()):
        raise InvitationIneligible('Recipient email is not verified and current')
    try:
        validate_email(email.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise InvitationIneligible('Recipient email is not verified and current') from None
    # Multiple active assignments for the same reviewer and round are ambiguous.
    duplicate = db.execute('''SELECT 1 FROM review_assignments a
        LEFT JOIN reviewer_invitations i ON i.assignment_id=a.assignment_id
        WHERE a.manuscript_id=? AND a.review_round=? AND a.reviewer_id=?
        AND a.assignment_id<>? AND a.status='assigned'
        AND (i.status IS NULL OR i.status <> 'declined') LIMIT 1''',
        (manuscript, round_no, recipient, assignment)).fetchone()
    if duplicate:
        raise InvitationIneligible('Reviewer invitation state is ambiguous')
    return Eligibility(lease.outbox_id, assignment, recipient, round_no)


def check_invitation_eligibility(db, lease, *, now=None):
    """Check under BEGIN IMMEDIATE; caller must commit or roll back promptly.

    Missing schema raises SQLite errors and cannot produce a positive result.
    This observation expires as soon as the transaction ends.
    """
    if not isinstance(lease, Lease):
        raise TypeError('Lease is required')
    if now is not None:
        _timestamp(now)
    _begin(db)
    stamp = _timestamp(datetime.now(timezone.utc) if now is None else now)
    return _check_locked(db, lease, stamp)


def start_eligible_attempt(db, lease, *, max_attempts, now=None):
    """Create a started attempt and check eligibility under one write lock.

    No commit or transport occurs. If validation fails, roll back the entire
    transaction started here; on success caller must explicitly commit before
    using the result. An uncertain commit is not permission to send.
    """
    current = datetime.now(timezone.utc) if now is None else now
    stamp = _timestamp(current)
    if db.in_transaction:
        raise ValueError('A fresh connection transaction is required')
    try:
        attempt = start_attempt(db, lease, max_attempts=max_attempts, now=current)
        eligibility = _check_locked(db, lease, _timestamp(datetime.now(timezone.utc)) if now is None else stamp)
        return attempt, eligibility
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
