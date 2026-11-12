from app.config import DATA_ROOT
from fastapi.responses import FileResponse
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
import logging
import sqlite3
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Literal
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User,
    Manuscript,
    ReviewAssignment,
    Review,
    Notification,
    PreReviewConfirmation,
    InitialEditingSubmission,
    InitialEditorInvitation,
    ReviewRoundFile,
    ReviewAssignmentFile,
    ReviewerInvitation,
)
from app.schemas import ReviewAssignmentCreate, ReviewAssignmentResponse
from app.routers.users import require_role
from app.routers.manuscripts import manuscript_has_pdf, ALLOWED_MANUSCRIPT_EXTENSIONS, can_access_manuscript
from app.review_files import get_or_create_round_one, resolve_assignment_file, validate_round_file
from app.reviewer_invitations import invitation_status, not_declined
from app.review_progress import review_progress
from app.email.enqueue_mode import EnqueueMode, OutboxSchemaUnavailable, check_outbox_schema, reviewer_email_enqueue_mode
from app.email.outbox import enqueue_reviewer_invitation


router = APIRouter(
    prefix="/assignments",
    tags=["Review Assignments"]
)
BASE_DIR = DATA_ROOT
logger = logging.getLogger(__name__)


def _sqlite_busy(error):
    original = getattr(error, 'orig', error)
    code = getattr(original, 'sqlite_errorcode', None)
    return code is not None and (code & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _rollback_or_unknown(db):
    try:
        db.rollback()
        return True
    except Exception:
        logger.error('reviewer_invitation_rollback_unknown')
        return False


_UNKNOWN = 'Invitation outcome could not be confirmed; refresh the reviewer assignment list before trying again.'


class _EnqueueFailure(Exception):
    def __init__(self, error):
        self.error = error

def assignment_to_response(
    db: Session,
    assignment: ReviewAssignment,
    manuscript: Manuscript
) -> ReviewAssignmentResponse:
    """Include manuscript information without changing database tables."""
    response = ReviewAssignmentResponse.model_validate(assignment)

    try:
        resolve_assignment_file(db, assignment, manuscript)
        available = True
    except HTTPException:
        available = False

    return response.model_copy(update={
        "invitation_status": invitation_status(db, assignment),
        "manuscript_title": manuscript.title,
        "manuscript_status": manuscript.status,
        "has_pdf": manuscript_has_pdf(manuscript),
        "review_file_available": available,
        "review_file_status": "available" if available else "unavailable",
    })


def assignments_to_responses(
    db: Session,
    assignments: list[ReviewAssignment]
) -> list[ReviewAssignmentResponse]:
    """Load related manuscripts in one query to avoid per-row DB lookups."""
    if not assignments:
        return []

    manuscript_ids = {
        assignment.manuscript_id
        for assignment in assignments
    }

    manuscripts = db.scalars(
        select(Manuscript).where(
            Manuscript.manuscript_id.in_(manuscript_ids)
        )
    ).all()

    by_id = {
        manuscript.manuscript_id: manuscript
        for manuscript in manuscripts
    }

    result = []

    for assignment in assignments:
        manuscript = by_id.get(assignment.manuscript_id)

        if manuscript is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Assignment references a missing manuscript"
            )

        result.append(
            assignment_to_response(db, assignment, manuscript)
        )

    return result


# Editor: Assign a reviewer to a manuscript
@router.post(
    "/",
    response_model=ReviewAssignmentResponse,
    status_code=status.HTTP_201_CREATED
)
def assign_reviewer(
    assignment_data: ReviewAssignmentCreate,
    current_user: Annotated[
        User,
        Depends(require_role("editor", "eic"))
    ],
    db: Session = Depends(get_db),
    enqueue_mode: EnqueueMode = Depends(reviewer_email_enqueue_mode),
):
    if not isinstance(enqueue_mode, EnqueueMode):
        raise HTTPException(status_code=503, detail='Reviewer invitation is temporarily unavailable.')
    if enqueue_mode is EnqueueMode.DISABLED:
        return _assign_reviewer(assignment_data, current_user, db, enqueue_mode)
    try:
        return _assign_reviewer(assignment_data, current_user, db, enqueue_mode)
    except SQLAlchemyError as error:
        if not _sqlite_busy(error):
            raise
        if not _rollback_or_unknown(db):
            raise HTTPException(status_code=503, detail=_UNKNOWN) from None
        raise HTTPException(status_code=503, detail='Reviewer invitation was not saved due to temporary database contention. Refresh the reviewer assignment list before retrying.') from None


def _assign_reviewer(assignment_data, current_user, db, enqueue_mode):
    # Step 1: Find the manuscript
    manuscript = db.get(
        Manuscript,
        assignment_data.manuscript_id
    )

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )


    # Step 2: Validate manuscript workflow status.
    if manuscript.status not in ("ready_for_peer_review", "revised"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not ready for reviewer assignment",
        )

    # Step 2.1: Verify author confirmation for the first review round.
    if manuscript.review_round == 1:
        confirmation = db.scalar(
            select(PreReviewConfirmation)
            .join(
                InitialEditingSubmission,
                PreReviewConfirmation.submission_id
                == InitialEditingSubmission.submission_id,
            )
            .where(
                PreReviewConfirmation.manuscript_id
                == manuscript.manuscript_id,
                PreReviewConfirmation.author_id
                == manuscript.author_id,
                PreReviewConfirmation.decision == "confirm",
                InitialEditingSubmission.status == "eic_approved",
                InitialEditingSubmission.manuscript_id
                == manuscript.manuscript_id,
            )
            .order_by(
                PreReviewConfirmation.confirmation_id.desc()
            )
        )

        if confirmation is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Author pre-review confirmation is required",
            )

    # Step 2.2: Verify that the current editor is assigned
    # to this manuscript.
    accepted_invitation = db.scalar(
        select(InitialEditorInvitation).where(
            InitialEditorInvitation.manuscript_id
            == manuscript.manuscript_id,
            InitialEditorInvitation.editor_id
            == current_user.user_id,
            InitialEditorInvitation.status == "accepted",
        )
    )

    if current_user.role == "editor" and accepted_invitation is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned editor for this manuscript",
        )

    # Step 3: Use the manuscript's current review round
    expected_round = manuscript.review_round

    if assignment_data.review_round != expected_round:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This manuscript requires review round {expected_round}"
        )

    # Step 4: Find the reviewer
    reviewer = db.get(
        User,
        assignment_data.reviewer_id
    )

    if reviewer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reviewer not found"
        )

    if reviewer.role != "reviewer":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected user is not a reviewer"
        )

    # Step 5: Prevent duplicate assignments in the same round
    existing_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == assignment_data.manuscript_id,
            ReviewAssignment.reviewer_id == assignment_data.reviewer_id,
            ReviewAssignment.review_round == expected_round
        )
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reviewer already assigned in this review round"
        )


    # The preflight is only reached by an explicitly overridden test dependency.
    # It precedes get_or_create_round_one, which can itself write and flush.
    if enqueue_mode is EnqueueMode.ENQUEUE_ONLY:
        try:
            check_outbox_schema(db)
        except (OutboxSchemaUnavailable, SQLAlchemyError, ImportError, OSError, AttributeError) as error:
            logger.error('reviewer_invitation_preflight_unavailable')
            if not _rollback_or_unknown(db):
                raise HTTPException(status_code=503, detail=_UNKNOWN) from None
            if _sqlite_busy(error):
                raise HTTPException(status_code=503, detail='Reviewer invitation is temporarily unavailable due to database contention. Refresh the reviewer assignment list before retrying.') from None
            raise HTTPException(status_code=503, detail='Reviewer invitation is unavailable because the notification queue is not ready.') from None

    def build_invitation():
        # Bind the assignment to the formally authorized file for this round.
        if expected_round == 1:
            round_file = get_or_create_round_one(db, manuscript)
        else:
            round_file = db.scalar(select(ReviewRoundFile).where(
                ReviewRoundFile.manuscript_id == manuscript.manuscript_id,
                ReviewRoundFile.review_round == expected_round,
            ))
            if round_file is None:
                raise HTTPException(status_code=409, detail="No recorded file for this review round; contact the editorial team")
            validate_round_file(round_file, manuscript, db)

        # Step 6: Create the assignment
        new_assignment = ReviewAssignment(
            manuscript_id=assignment_data.manuscript_id,
            reviewer_id=assignment_data.reviewer_id,
            assigned_by=current_user.user_id,
            review_round=expected_round,
            status="assigned"
        )

        db.add(new_assignment)
        db.flush()
        db.add(ReviewAssignmentFile(assignment_id=new_assignment.assignment_id, round_file_id=round_file.round_file_id))
        db.add(ReviewerInvitation(assignment_id=new_assignment.assignment_id, status="pending"))

        # Step 7: Notify the assigned reviewer
        notification = Notification(
            user_id=reviewer.user_id,
            manuscript_id=manuscript.manuscript_id,
            notification_type="review_invitation",
            message=(
                f"You have been invited to review "
                f"Manuscript #{manuscript.manuscript_id}: "
                f"{manuscript.title} (Round {expected_round})."
            ),
            is_read=False
        )

        db.add(notification)

        return new_assignment

    if enqueue_mode is EnqueueMode.DISABLED:
        new_assignment = build_invitation()
        db.commit()
        db.refresh(new_assignment)
        return assignment_to_response(db, new_assignment, manuscript)

    try:
        new_assignment = build_invitation()
        try:
            enqueue_reviewer_invitation(db, new_assignment.assignment_id)
        except Exception as error:
            # Log only a category, not SQL, exception text or manuscript data.
            logger.error('reviewer_invitation_enqueue_failed: %s', type(error).__name__)
            raise _EnqueueFailure(error) from None
    except HTTPException:
        if not _rollback_or_unknown(db):
            raise HTTPException(status_code=503, detail=_UNKNOWN) from None
        raise
    except Exception as error:
        if not _rollback_or_unknown(db):
            raise HTTPException(status_code=503, detail=_UNKNOWN) from None
        cause = error.error if isinstance(error, _EnqueueFailure) else error
        if _sqlite_busy(cause):
            raise HTTPException(status_code=503, detail='Reviewer invitation was not saved due to temporary database contention. Refresh the reviewer assignment list before retrying.') from None
        if isinstance(error, _EnqueueFailure):
            raise HTTPException(status_code=503, detail='Reviewer invitation was not saved because notification queueing failed.') from None
        if isinstance(error, SQLAlchemyError):
            logger.error('reviewer_invitation_database_failure')
            raise HTTPException(status_code=503, detail='Reviewer invitation was not saved due to a database error.') from None
        raise

    try:
        db.commit()
        db.refresh(new_assignment)
        return assignment_to_response(db, new_assignment, manuscript)
    except Exception:
        # A commit acknowledgement, refresh or serialization error may occur
        # after commit. Never claim that the invitation was not saved.
        logger.error('reviewer_invitation_commit_or_refresh_unknown')
        _rollback_or_unknown(db)
        raise HTTPException(status_code=503, detail=_UNKNOWN) from None



class InvitationResponse(BaseModel):
    decision: Literal["accept", "decline"]


@router.post("/{assignment_id}/invitation-response")
def respond_reviewer_invitation(assignment_id: int, data: InvitationResponse, current_user: User = Depends(require_role("reviewer")), db: Session = Depends(get_db)):
    assignment = db.get(ReviewAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if assignment.reviewer_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="This invitation belongs to another reviewer")
    manuscript = db.get(Manuscript, assignment.manuscript_id)
    if manuscript is None or manuscript.status not in ("ready_for_peer_review", "revised") or manuscript.review_round != assignment.review_round:
        raise HTTPException(status_code=409, detail="This review round is no longer open")
    new_status = "accepted" if data.decision == "accept" else "declined"
    result = db.execute(update(ReviewerInvitation).where(ReviewerInvitation.assignment_id == assignment_id, ReviewerInvitation.status == "pending").values(status=new_status, responded_at=datetime.now(timezone.utc)))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Invitation is not pending")
    db.add(Notification(user_id=assignment.assigned_by, manuscript_id=assignment.manuscript_id, notification_type="review_invitation_response", message=f"Reviewer {current_user.name} {new_status} the invitation for manuscript #{assignment.manuscript_id}, round {assignment.review_round}.", is_read=False))
    db.commit()
    return {"assignment_id": assignment_id, "invitation_status": new_status}



# Editor: View all review assignments
@router.get(
    "/",
    response_model=list[ReviewAssignmentResponse],
    summary="Get All Review Assignments"
)
def get_all_assignments(
    current_user: Annotated[
        User,
        Depends(require_role("editor"))
    ],
    db: Session = Depends(get_db)
):
    assignments = db.scalars(
        select(ReviewAssignment).order_by(
            ReviewAssignment.assignment_id
        )
    ).all()

    accessible = []
    for assignment in assignments:
        manuscript = db.get(Manuscript, assignment.manuscript_id)
        if manuscript and can_access_manuscript(db, manuscript, current_user):
            accessible.append(assignment)
    return assignments_to_responses(db, accessible)


# Reviewer: View their own assignments
@router.get(
    "/my",
    response_model=list[ReviewAssignmentResponse],
    summary="Get My Review Assignments"
)
def get_my_assignments(
    current_user: Annotated[
        User,
        Depends(require_role("reviewer"))
    ],
    db: Session = Depends(get_db)
):
    assignments = db.scalars(
        select(ReviewAssignment)
        .where(
            ReviewAssignment.reviewer_id == current_user.user_id
        )
        .order_by(
            ReviewAssignment.assignment_id
        )
    ).all()

    return assignments_to_responses(db, assignments)


# Editor: View all assignments for a manuscript
@router.get(
    "/manuscript/{manuscript_id}",
    response_model=list[ReviewAssignmentResponse],
    summary="Get Manuscript Review Progress"
)
def get_manuscript_review_progress(
    manuscript_id: int,
    current_user: Annotated[
        User,
        Depends(require_role("editor", "eic"))
    ],
    db: Session = Depends(get_db)
):
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    if not can_access_manuscript(db, manuscript, current_user):
        raise HTTPException(status_code=403, detail="You are not an assigned editor")

    assignments = db.scalars(
        select(ReviewAssignment)
        .where(
            ReviewAssignment.manuscript_id == manuscript_id
        )
        .order_by(
            ReviewAssignment.review_round,
            ReviewAssignment.assignment_id
        )
    ).all()

    return [
        assignment_to_response(db, assignment, manuscript)
        for assignment in assignments
    ]


# Editor: Get current review round summary
@router.get(
    "/manuscript/{manuscript_id}/summary",
    summary="Get Manuscript Review Summary"
)
def get_manuscript_review_summary(
    manuscript_id: int,
    current_user: Annotated[
        User,
        Depends(require_role("editor", "eic"))
    ],
    db: Session = Depends(get_db)
):
    # Step 1: Find manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    if not can_access_manuscript(db, manuscript, current_user):
        raise HTTPException(status_code=403, detail="You are not an assigned editor")

    # Step 2: Use current review round
    review_round = manuscript.review_round

    # Step 3: Get assignments for current round
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == review_round,
            not_declined(),
        )
    ).all()

    # Step 4: Find completed reviews
    assignment_ids = [
        assignment.assignment_id
        for assignment in assignments
    ]

    reviewed_ids = set()

    if assignment_ids:
        reviewed_ids = set(
            db.scalars(
                select(Review.assignment_id).where(
                    Review.assignment_id.in_(assignment_ids)
                )
            ).all()
        )

    # Step 5: Calculate progress
    total_reviewers = len(assignments)

    completed_reviews = sum(
        assignment.assignment_id in reviewed_ids
        for assignment in assignments
    )

    pending_reviews = total_reviewers - completed_reviews

    # Step 6: Return summary
    return {
        "manuscript_id": manuscript_id,
        "decision_progress": review_progress(db, manuscript, review_round),
        "review_round": review_round,
        "total_reviewers": total_reviewers,
        "completed_reviews": completed_reviews,
        "pending_reviews": pending_reviews,
        "all_reviews_completed": (
            total_reviewers > 0
            and pending_reviews == 0
        )
    }

@router.get(
    "/{assignment_id}/download",
    summary="Download Assigned Peer-review PDF",
)
def download_assigned_review_pdf(
    assignment_id: int,
    current_user: Annotated[
        User,
        Depends(require_role("reviewer")),
    ],
    db: Session = Depends(get_db),
):
    # Step 1: Find the review assignment.
    assignment = db.get(ReviewAssignment, assignment_id)

    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail="Review assignment not found",
        )

    # Step 2: Only the assigned reviewer can download.
    if assignment.reviewer_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You are not assigned to this review",
        )

    manuscript = db.get(Manuscript, assignment.manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    path = resolve_assignment_file(db, assignment, manuscript)
    extension = path.suffix.lower()
    return FileResponse(
        path=path,
        media_type=ALLOWED_MANUSCRIPT_EXTENSIONS[extension],
        filename=f"manuscript_{manuscript.manuscript_id}_review_round_{assignment.review_round}{extension}",
    )
