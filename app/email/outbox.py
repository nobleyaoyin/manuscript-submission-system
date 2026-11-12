"""Same-session persistence for a prospective reviewer invitation email intent.

This module does not integrate with workflow routes or send messages.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EmailOutbox, Manuscript, NotificationEvent, ReviewAssignment, ReviewerInvitation, User

EVENT_TYPE = 'reviewer.invitation.created'
TEMPLATE_KEY = 'reviewer_invitation'
TEMPLATE_VERSION = 'v1'


class InvitationEligibilityError(ValueError):
    """The assignment cannot currently receive a new invitation intent."""


class OutboxConflictError(ValueError):
    """Existing event or outbox data conflicts with the intended recipient."""


@dataclass(frozen=True)
class EnqueueResult:
    event_id: str
    outbox_id: str
    created_event: bool
    created_outbox: bool


def _expected_unique(error: IntegrityError, columns: str) -> bool:
    # SQLite's exact constraint message distinguishes the expected race from
    # PK, FK, CHECK and unrelated integrity failures. Never ignore the latter.
    return str(error.orig) == f'UNIQUE constraint failed: {columns}'


def _event(db: Session, assignment_id: int):
    return db.scalar(select(NotificationEvent).where(
        NotificationEvent.event_type == EVENT_TYPE,
        NotificationEvent.invitation_assignment_id == assignment_id,
    ).execution_options(populate_existing=True))


def _outboxes(db: Session, event_id: str):
    return list(db.scalars(select(EmailOutbox).where(
        EmailOutbox.event_id == event_id,
    ).execution_options(populate_existing=True)).all())


def enqueue_reviewer_invitation(db: Session, assignment_id: int) -> EnqueueResult:
    """Queue one pending invite in the caller's SQLite transaction; never commit.

    Caller must commit or roll back, and retry the whole transaction if SQLite
    reports a busy/locked snapshot. No transport or email address is used.
    """
    if type(assignment_id) is not int or assignment_id <= 0:
        raise InvitationEligibilityError('Invalid assignment ID')
    # Persist the caller's pending invitation/assignment before selecting with
    # autoflush=False. Errors remain visible and are not rolled back here.
    db.flush()
    connection = db.connection()
    if connection.dialect.name != 'sqlite':
        raise ValueError('This helper requires SQLite')
    # Python sqlite3 legacy transaction mode can leave a SELECT-only session
    # without a physical BEGIN. A first SAVEPOINT would then commit on RELEASE.
    # Start the physical transaction corresponding to the caller's session.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')

    assignment = db.get(ReviewAssignment, assignment_id, populate_existing=True)
    invitation = db.get(ReviewerInvitation, assignment_id, populate_existing=True)
    if assignment is None or invitation is None or invitation.assignment_id != assignment.assignment_id:
        raise InvitationEligibilityError('Reviewer invitation is unavailable')
    reviewer = db.get(User, assignment.reviewer_id, populate_existing=True)
    manuscript = db.get(Manuscript, assignment.manuscript_id, populate_existing=True)
    if not reviewer or reviewer.role != 'reviewer':
        raise InvitationEligibilityError('Reviewer is unavailable')
    if (invitation.status != 'pending' or assignment.status != 'assigned' or not manuscript
            or manuscript.status not in ('ready_for_peer_review', 'revised')
            or manuscript.review_round != assignment.review_round):
        raise InvitationEligibilityError('Reviewer invitation is not currently eligible')

    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    event = _event(db, assignment_id)
    created_event = False
    if event is None:
        candidate = NotificationEvent(event_id=str(uuid4()), event_type=EVENT_TYPE,
                                      invitation_assignment_id=assignment_id,
                                      created_at=timestamp, schema_version=1)
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            event, created_event = candidate, True
        except IntegrityError as error:
            if not _expected_unique(error, 'notification_events.event_type, notification_events.invitation_assignment_id'):
                raise
            event = _event(db, assignment_id)
            if event is None:
                raise
    if event.schema_version != 1:
        raise OutboxConflictError('Existing event version conflicts')

    existing = _outboxes(db, event.event_id)
    if any(item.recipient_user_id != assignment.reviewer_id or item.template_key != TEMPLATE_KEY
           or item.template_version != TEMPLATE_VERSION for item in existing) or len(existing) > 1:
        raise OutboxConflictError('Existing outbox intent conflicts')
    if existing:
        return EnqueueResult(event.event_id, existing[0].outbox_id, created_event, False)

    candidate = EmailOutbox(outbox_id=str(uuid4()), event_id=event.event_id,
                            recipient_user_id=assignment.reviewer_id,
                            template_key=TEMPLATE_KEY, template_version=TEMPLATE_VERSION,
                            status='pending', attempt_count=0, next_attempt_at=timestamp,
                            created_at=timestamp, updated_at=timestamp)
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return EnqueueResult(event.event_id, candidate.outbox_id, created_event, True)
    except IntegrityError as error:
        if not _expected_unique(error, 'email_outbox.event_id, email_outbox.recipient_user_id'):
            raise
        existing = _outboxes(db, event.event_id)
        if len(existing) != 1 or existing[0].recipient_user_id != assignment.reviewer_id or existing[0].template_key != TEMPLATE_KEY or existing[0].template_version != TEMPLATE_VERSION:
            raise OutboxConflictError('Existing outbox intent conflicts') from error
        return EnqueueResult(event.event_id, existing[0].outbox_id, created_event, False)
