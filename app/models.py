
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    DateTime,
    ForeignKey,
    Integer,
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
