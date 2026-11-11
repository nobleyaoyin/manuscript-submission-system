
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    DateTime,
    ForeignKey,
    Integer,
    Boolean,
    Column,
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
