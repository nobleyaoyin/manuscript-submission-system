
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Boolean,
    Column,
    CheckConstraint,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# User table
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    name: Mapped[str] = mapped_column(
        String(100)
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255)
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="author"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# Manuscript table
class Manuscript(Base):
    __tablename__ = "manuscripts"

    manuscript_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    title: Mapped[str] = mapped_column(
        String(255)
    )

    abstract: Mapped[str] = mapped_column(
        Text
    )

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id")
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="submitted"
    )

    # Current review round of the manuscript
    review_round: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    # Path to the uploaded manuscript PDF
    file_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )


# Review Assignment table
class ReviewAssignment(Base):
    __tablename__ = "review_assignments"

    assignment_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    manuscript_id: Mapped[int] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False
    )

    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False
    )

    assigned_by: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False
    )

    review_round: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="assigned",
        nullable=False
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class ManuscriptReviewPolicy(Base):
    __tablename__ = "manuscript_review_policies"
    __table_args__ = (CheckConstraint("minimum_completed_reviewers = 2"),)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.manuscript_id"), primary_key=True)
    minimum_completed_reviewers: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class ReviewerInvitation(Base):
    __tablename__ = "reviewer_invitations"
    __table_args__ = (CheckConstraint("status IN ('pending', 'accepted', 'declined')"),)

    assignment_id: Mapped[int] = mapped_column(ForeignKey("review_assignments.assignment_id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    invited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


_OUTBOX_UTC_GLOB = ('[0-9]' * 4 + '-' + '[0-9]' * 2 + '-' + '[0-9]' * 2 +
                    'T' + '[0-9]' * 2 + ':' + '[0-9]' * 2 + ':' + '[0-9]' * 2 +
                    '.' + '[0-9]' * 6 + 'Z')


def _outbox_utc_check(column, nullable=False):
    pattern = f"{column} GLOB '{_OUTBOX_UTC_GLOB}'"
    return f'{column} IS NULL OR ({pattern})' if nullable else pattern


class NotificationEvent(Base):
    __tablename__ = 'notification_events'
    __table_args__ = (
        CheckConstraint("event_type = 'reviewer.invitation.created'", name='ck_notification_event_type'),
        CheckConstraint('schema_version = 1', name='ck_notification_event_version'),
        CheckConstraint(_outbox_utc_check('created_at'), name='ck_notification_event_created_utc'),
        UniqueConstraint('event_type', 'invitation_assignment_id', name='uq_notification_event_source'),
    )
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    invitation_assignment_id: Mapped[int] = mapped_column(ForeignKey('reviewer_invitations.assignment_id', ondelete='RESTRICT'), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class EmailOutbox(Base):
    __tablename__ = 'email_outbox'
    __table_args__ = (
        UniqueConstraint('event_id', 'recipient_user_id', name='uq_email_outbox_event_recipient'),
        CheckConstraint("status IN ('pending','processing','retry_wait','blocked_verification','accepted','failed','unknown','cancelled')", name='ck_email_outbox_status'),
        CheckConstraint("typeof(attempt_count) = 'integer' AND attempt_count >= 0", name='ck_email_outbox_attempt_count'),
        CheckConstraint("(status = 'processing' AND lease_token IS NOT NULL AND length(trim(lease_token)) > 0 AND lease_until IS NOT NULL) OR (status <> 'processing' AND lease_token IS NULL AND lease_until IS NULL)", name='ck_email_outbox_lease'),
        CheckConstraint("(status IN ('pending','retry_wait') AND next_attempt_at IS NOT NULL) OR (status NOT IN ('pending','retry_wait') AND next_attempt_at IS NULL)", name='ck_email_outbox_due'),
        CheckConstraint("(status = 'accepted' AND accepted_at IS NOT NULL) OR (status <> 'accepted' AND accepted_at IS NULL)", name='ck_email_outbox_accepted'),
        CheckConstraint(_outbox_utc_check('created_at'), name='ck_email_outbox_created_utc'),
        CheckConstraint(_outbox_utc_check('updated_at'), name='ck_email_outbox_updated_utc'),
        CheckConstraint(_outbox_utc_check('next_attempt_at', True), name='ck_email_outbox_due_utc'),
        CheckConstraint(_outbox_utc_check('lease_until', True), name='ck_email_outbox_lease_utc'),
        CheckConstraint(_outbox_utc_check('accepted_at', True), name='ck_email_outbox_accepted_utc'),
        CheckConstraint('provider_message_id IS NULL OR length(provider_message_id) BETWEEN 1 AND 255', name='ck_email_outbox_provider_id'),
        CheckConstraint('terminal_reason IS NULL OR length(terminal_reason) BETWEEN 1 AND 64', name='ck_email_outbox_reason'),
        Index('ix_email_outbox_due', 'status', 'next_attempt_at'),
        Index('ix_email_outbox_lease', 'status', 'lease_until'),
    )
    outbox_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    event_id: Mapped[str] = mapped_column(ForeignKey('notification_events.event_id', ondelete='RESTRICT'), nullable=False)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    next_attempt_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmailDeliveryAttempt(Base):
    __tablename__ = 'email_delivery_attempts'
    __table_args__ = (
        UniqueConstraint('outbox_id', 'attempt_number', name='uq_email_attempt_number'),
        CheckConstraint("typeof(attempt_number) = 'integer' AND attempt_number > 0", name='ck_email_attempt_number'),
        CheckConstraint("outcome IN ('started','accepted','retryable','permanent','unknown')", name='ck_email_attempt_outcome'),
        CheckConstraint("(outcome = 'started' AND finished_at IS NULL) OR (outcome <> 'started' AND finished_at IS NOT NULL)", name='ck_email_attempt_finished'),
        CheckConstraint(_outbox_utc_check('started_at'), name='ck_email_attempt_started_utc'),
        CheckConstraint(_outbox_utc_check('finished_at', True), name='ck_email_attempt_finished_utc'),
        CheckConstraint('length(trim(lease_token)) > 0', name='ck_email_attempt_lease'),
        CheckConstraint('error_code IS NULL OR length(error_code) BETWEEN 1 AND 64', name='ck_email_attempt_error_code'),
        CheckConstraint('provider_message_id IS NULL OR length(provider_message_id) BETWEEN 1 AND 255', name='ck_email_attempt_provider_id'),
        Index('ix_email_attempt_outbox_started', 'outbox_id', 'started_at'),
    )
    attempt_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    outbox_id: Mapped[str] = mapped_column(ForeignKey('email_outbox.outbox_id', ondelete='RESTRICT'), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewRoundFile(Base):
    __tablename__ = "review_round_files"
    __table_args__ = (
        UniqueConstraint("manuscript_id", "review_round"),
        CheckConstraint("review_round >= 1"),
        CheckConstraint("source_kind IN ('initial_editing', 'author_revision')"),
        CheckConstraint("(review_round = 1 AND source_kind = 'initial_editing' AND confirmation_id IS NOT NULL) OR (review_round > 1 AND source_kind = 'author_revision' AND confirmation_id IS NULL)"),
    )
    round_file_id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.manuscript_id"))
    review_round: Mapped[int] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(Text)
    confirmation_id: Mapped[int | None] = mapped_column(ForeignKey("pre_review_confirmations.confirmation_id"), nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReviewAssignmentFile(Base):
    __tablename__ = "review_assignment_files"
    __table_args__ = (Index("ix_review_assignment_files_round_file_id", "round_file_id"),)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("review_assignments.assignment_id"), primary_key=True)
    round_file_id: Mapped[int] = mapped_column(ForeignKey("review_round_files.round_file_id"))
    bound_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# Review table
class Review(Base):
    __tablename__ = "reviews"

    review_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("review_assignments.assignment_id"),
        unique=True
    )

    comments: Mapped[str] = mapped_column(
        Text
    )

    recommendation: Mapped[str] = mapped_column(
        String(30)
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

# Notification table
class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    # User who receives the notification
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False,
        index=True
    )

    # Related manuscript (optional)
    manuscript_id: Mapped[int | None] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=True
    )

    # Notification category
    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    # Notification content
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    # Read / unread status
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # Creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

# EIC Initial Scan history table
class EICInitialScan(Base):
    __tablename__ = "eic_initial_scans"

    scan_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    manuscript_id: Mapped[int] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True
    )

    eic_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False
    )

    decision: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    )

    comments: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


# Initial Editor Invitation table
class InitialEditorInvitation(Base):
    __tablename__ = "initial_editor_invitations"

    invitation_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    manuscript_id: Mapped[int] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True
    )

    editor_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False,
        index=True
    )

    invited_by: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False
    )

    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

# Initial Editing Submission table
class InitialEditingSubmission(Base):
    __tablename__ = "initial_editing_submissions"

    submission_id: Mapped[int] = mapped_column(
        primary_key=True
    )

    manuscript_id: Mapped[int] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True
    )

    editor_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False,
        index=True
    )

    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    comments: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    author_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    editor_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    editor_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    eic_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    eic_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    eic_approved_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.user_id"),
        nullable=True
    )


    status: Mapped[str] = mapped_column(
        String(30),
        default="pending_author",
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

class PreReviewConfirmation(Base):
    __tablename__ = "pre_review_confirmations"

    confirmation_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    manuscript_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True,
    )

    submission_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("initial_editing_submissions.submission_id"),
        nullable=False,
        index=True,
    )

    author_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id"),
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    decision: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    comments: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

class EICDecision(Base):
    __tablename__ = "eic_decisions"

    decision_id = Column(Integer, primary_key=True, index=True)

    manuscript_id = Column(
        Integer,
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True,
    )

    eic_id = Column(
        Integer,
        ForeignKey("users.user_id"),
        nullable=False,
    )

    review_round = Column(Integer, nullable=False)

    decision = Column(String(30), nullable=False)

    comments = Column(Text, nullable=False)

    decided_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

class AuthorAgreement(Base):
    __tablename__ = "author_agreements"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined')"),
        CheckConstraint("(status = 'pending' AND responded_at IS NULL) OR (status IN ('accepted', 'declined') AND responded_at IS NOT NULL)"),
        UniqueConstraint("decision_id"),
    )
    agreement_id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey("manuscripts.manuscript_id"), nullable=False)
    decision_id: Mapped[int] = mapped_column(ForeignKey("eic_decisions.decision_id"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    agreement_version: Mapped[str] = mapped_column(String(40), nullable=False)
    agreement_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FinalEditorInvitation(Base):
    __tablename__ = 'final_editor_invitations'
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined')"),
        Index('uq_final_editor_active', 'manuscript_id', unique=True, sqlite_where=text("status IN ('pending', 'accepted')")),
    )
    invitation_id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey('manuscripts.manuscript_id'), nullable=False)
    editor_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'), nullable=False)
    invited_by: Mapped[int] = mapped_column(ForeignKey('users.user_id'), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    invited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FinalEditingSubmission(Base):
    __tablename__ = "final_editing_submissions"

    submission_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    manuscript_id: Mapped[int] = mapped_column(
        ForeignKey("manuscripts.manuscript_id"),
        nullable=False,
        index=True,
    )

    editor_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=False,
        index=True,
    )

    # Final edited PDF submitted by the editor.
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # Editor's explanation of the changes.
    comments: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Workflow status for this PDF version.
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending_author",
        nullable=False,
    )

    # Author review.
    author_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    author_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # EIC approval.
    eic_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    eic_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    eic_approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id"),
        nullable=True,
    )

    # Submission timestamp.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class FinalEditingSubmissionInvitation(Base):
    __tablename__ = 'final_editing_submission_invitations'
    submission_id: Mapped[int] = mapped_column(ForeignKey('final_editing_submissions.submission_id'), primary_key=True)
    invitation_id: Mapped[int] = mapped_column(ForeignKey('final_editor_invitations.invitation_id'), nullable=False)


class FormatterInvitation(Base):
    __tablename__ = 'formatter_invitations'
    __table_args__ = (Index('uq_formatter_active', 'manuscript_id', unique=True, sqlite_where=text("status IN ('pending', 'accepted')")),)
    invitation_id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey('manuscripts.manuscript_id'))
    formatter_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    invited_by: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    final_submission_id: Mapped[int] = mapped_column(ForeignKey('final_editing_submissions.submission_id'))
    status: Mapped[str] = mapped_column(String(20))
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FormattingSubmission(Base):
    __tablename__ = 'formatting_submissions'
    __table_args__ = (Index('uq_formatting_current', 'manuscript_id', unique=True, sqlite_where=text("status IN ('pending_author', 'pending_eic', 'approved')")),)
    submission_id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_id: Mapped[int] = mapped_column(ForeignKey('manuscripts.manuscript_id'))
    invitation_id: Mapped[int] = mapped_column(ForeignKey('formatter_invitations.invitation_id'))
    file_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    comments: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    author_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eic_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    eic_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eic_id: Mapped[int | None] = mapped_column(ForeignKey('users.user_id'), nullable=True)


class PrototypePublication(Base):
    __tablename__ = 'prototype_publications'
    manuscript_id: Mapped[int] = mapped_column(ForeignKey('manuscripts.manuscript_id'), primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey('formatting_submissions.submission_id'), unique=True)
    published_by: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    doi_status: Mapped[str] = mapped_column(String(30), default='pending_external')


# Email verification status for each user.
class UserEmailVerification(Base):
    __tablename__ = "user_email_verifications"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# One-time email verification challenges.
class EmailVerificationChallenge(Base):
    __tablename__ = "email_verification_challenges"

    challenge_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_digest: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class AccountLifecycleEvent(Base):
    __tablename__ = 'account_lifecycle_events'
    __table_args__ = (
        CheckConstraint('length(event_id) BETWEEN 1 AND 64'),
        CheckConstraint("typeof(sequence) = 'integer' AND sequence >= 1"),
        CheckConstraint("from_state IS NULL OR from_state IN ('active','disabled')"),
        CheckConstraint("to_state IN ('active','disabled')"),
        CheckConstraint("typeof(auth_generation) = 'integer' AND auth_generation >= 1"),
        CheckConstraint(_outbox_utc_check('occurred_at')),
        CheckConstraint("reason_code IN ('initial_enrollment','administrative_disable','administrative_reactivation')"),
        CheckConstraint("(sequence = 1 AND from_state IS NULL AND to_state = 'active' AND reason_code = 'initial_enrollment') OR (sequence > 1 AND from_state IS NOT NULL AND from_state = 'active' AND to_state = 'disabled' AND reason_code = 'administrative_disable') OR (sequence > 1 AND from_state IS NOT NULL AND from_state = 'disabled' AND to_state = 'active' AND reason_code = 'administrative_reactivation')"),
        UniqueConstraint('user_id', 'sequence'),
        UniqueConstraint('event_id', 'user_id', 'sequence', 'to_state', 'auth_generation'),
        Index('ix_lifecycle_events_actor_time', 'actor_user_id', 'occurred_at'),
    )
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    auth_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)


class AccountLifecycle(Base):
    __tablename__ = 'account_lifecycle'
    __table_args__ = (
        CheckConstraint("state IN ('active','disabled')"),
        CheckConstraint("typeof(sequence) = 'integer' AND sequence >= 1"),
        CheckConstraint("typeof(auth_generation) = 'integer' AND auth_generation >= 1"),
        ForeignKeyConstraint(['last_event_id', 'user_id', 'sequence', 'state', 'auth_generation'],
                             ['account_lifecycle_events.event_id', 'account_lifecycle_events.user_id',
                              'account_lifecycle_events.sequence', 'account_lifecycle_events.to_state',
                              'account_lifecycle_events.auth_generation'], ondelete='RESTRICT'),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id', ondelete='RESTRICT'), primary_key=True, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    auth_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    last_event_id: Mapped[str] = mapped_column(Text, nullable=False)


class ReviewerInvitationInvalidation(Base):
    __tablename__ = 'reviewer_invitation_invalidations'
    __table_args__ = (
        CheckConstraint('length(invalidation_id) BETWEEN 1 AND 64'),
        CheckConstraint("kind IN ('cancelled','superseded')"),
        CheckConstraint(_outbox_utc_check('occurred_at')),
        CheckConstraint("reason_code IN ('editorial_cancellation','reviewer_unavailable','replacement_selected')"),
        CheckConstraint("(kind = 'cancelled' AND replacement_assignment_id IS NULL AND reason_code IN ('editorial_cancellation','reviewer_unavailable')) OR (kind = 'superseded' AND replacement_assignment_id IS NOT NULL AND replacement_assignment_id <> assignment_id AND reason_code = 'replacement_selected')"),
        UniqueConstraint('assignment_id'),
        Index('ix_reviewer_invalidation_actor_time', 'actor_user_id', 'occurred_at'),
        Index('ix_reviewer_invalidation_replacement', 'replacement_assignment_id'),
        Index('uq_invalidation_replacement', 'replacement_assignment_id', unique=True,
              sqlite_where=text('replacement_assignment_id IS NOT NULL')),
    )
    invalidation_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('reviewer_invitations.assignment_id', ondelete='RESTRICT'), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    replacement_assignment_id: Mapped[int | None] = mapped_column(ForeignKey('reviewer_invitations.assignment_id', ondelete='RESTRICT'), nullable=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
