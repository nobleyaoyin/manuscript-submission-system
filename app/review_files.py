"""Validation and resolution of immutable review-round file references."""
from pathlib import Path
from app.config import DATA_ROOT, guard_demo_path

from fastapi import HTTPException
from sqlalchemy import select

from app.models import InitialEditingSubmission, PreReviewConfirmation, ReviewAssignmentFile, ReviewRoundFile
from app.reviewer_invitations import may_review
from app.routers.manuscripts import ALLOWED_MANUSCRIPT_EXTENSIONS, MAX_MANUSCRIPT_FILE_SIZE, validate_manuscript_file

BASE_DIR = DATA_ROOT


def validate_round_file(record, manuscript, db):
    if record.manuscript_id != manuscript.manuscript_id:
        raise HTTPException(409, "Review file is unavailable")
    if record.review_round == 1 and record.source_kind == "initial_editing":
        confirmation = db.get(PreReviewConfirmation, record.confirmation_id)
        submission = db.get(InitialEditingSubmission, confirmation.submission_id) if confirmation else None
        if not (confirmation and submission and confirmation.manuscript_id == manuscript.manuscript_id
                and confirmation.author_id == manuscript.author_id and confirmation.decision == "confirm"
                and submission.manuscript_id == manuscript.manuscript_id and submission.status == "eic_approved"
                and confirmation.file_path == submission.file_path == record.file_path):
            raise HTTPException(409, "Review file is unavailable")
        root = BASE_DIR / "uploads" / "initial_editing" / str(manuscript.manuscript_id)
        allowed = {".pdf"}
    elif record.review_round > 1 and record.source_kind == "author_revision" and record.confirmation_id is None:
        root = BASE_DIR / "uploads" / "manuscripts" / str(manuscript.manuscript_id)
        allowed = set(ALLOWED_MANUSCRIPT_EXTENSIONS)
    else:
        raise HTTPException(409, "Review file is unavailable")
    path = guard_demo_path(BASE_DIR / record.file_path).resolve()
    guard_demo_path(root)
    if path.parent != root.resolve() or path.suffix.lower() not in allowed or not path.is_file():
        raise HTTPException(409, "Review file is unavailable")
    try:
        with path.open("rb") as file:
            content = file.read(MAX_MANUSCRIPT_FILE_SIZE + 1)
        validate_manuscript_file(path.name, content)
    except (OSError, HTTPException):
        raise HTTPException(409, "Review file is unavailable")
    return path


def resolve_assignment_file(db, assignment, manuscript):
    binding = db.get(ReviewAssignmentFile, assignment.assignment_id)
    record = db.get(ReviewRoundFile, binding.round_file_id) if binding else None
    if not record or not may_review(db, assignment) or assignment.status not in ("assigned", "completed") or record.review_round != assignment.review_round:
        raise HTTPException(409, "Review file is unavailable; contact the editorial team")
    return validate_round_file(record, manuscript, db)


def get_or_create_round_one(db, manuscript):
    record = db.scalar(select(ReviewRoundFile).where(ReviewRoundFile.manuscript_id == manuscript.manuscript_id, ReviewRoundFile.review_round == 1))
    if record:
        validate_round_file(record, manuscript, db)
        return record
    confirmations = db.scalars(select(PreReviewConfirmation).join(InitialEditingSubmission, PreReviewConfirmation.submission_id == InitialEditingSubmission.submission_id).where(
        PreReviewConfirmation.manuscript_id == manuscript.manuscript_id,
        PreReviewConfirmation.author_id == manuscript.author_id,
        PreReviewConfirmation.decision == "confirm",
        InitialEditingSubmission.manuscript_id == manuscript.manuscript_id,
        InitialEditingSubmission.status == "eic_approved",
    )).all()
    if len(confirmations) != 1:
        raise HTTPException(409, "A unique confirmed review file is required")
    confirmation = confirmations[0]
    record = ReviewRoundFile(manuscript_id=manuscript.manuscript_id, review_round=1, source_kind="initial_editing", confirmation_id=confirmation.confirmation_id, file_path=confirmation.file_path)
    validate_round_file(record, manuscript, db)
    db.add(record)
    db.flush()
    return record
