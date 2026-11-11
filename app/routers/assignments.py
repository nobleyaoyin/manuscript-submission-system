from pathlib import Path
from fastapi.responses import FileResponse
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
)
from app.schemas import ReviewAssignmentCreate, ReviewAssignmentResponse
from app.routers.users import require_role
from app.routers.manuscripts import manuscript_has_pdf


router = APIRouter(
    prefix="/assignments",
    tags=["Review Assignments"]
)
BASE_DIR = Path(__file__).resolve().parents[2]

def assignment_to_response(
    assignment: ReviewAssignment,
    manuscript: Manuscript
) -> ReviewAssignmentResponse:
    """Include manuscript information without changing database tables."""
    response = ReviewAssignmentResponse.model_validate(assignment)

    return response.model_copy(update={
        "manuscript_title": manuscript.title,
        "manuscript_status": manuscript.status,
        "has_pdf": manuscript_has_pdf(manuscript),
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
            assignment_to_response(assignment, manuscript)
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
        Depends(require_role("editor"))
    ],
    db: Session = Depends(get_db)
):
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

    if accepted_invitation is None:
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


    # Step 6: Create the assignment
    new_assignment = ReviewAssignment(
        manuscript_id=assignment_data.manuscript_id,
        reviewer_id=assignment_data.reviewer_id,
        assigned_by=current_user.user_id,
        review_round=expected_round,
        status="assigned"
    )

    db.add(new_assignment)

    # Step 7: Notify the assigned reviewer
    notification = Notification(
        user_id=reviewer.user_id,
        manuscript_id=manuscript.manuscript_id,
        notification_type="review_assigned",
        message=(
            f"You have been assigned to review "
            f"Manuscript #{manuscript.manuscript_id}: "
            f"{manuscript.title} (Round {expected_round})."
        ),
        is_read=False
    )

    db.add(notification)

    # Step 8: Save assignment and notification together
    db.commit()
    db.refresh(new_assignment)

    return assignment_to_response(new_assignment, manuscript)



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

    return assignments_to_responses(db, assignments)


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
        Depends(require_role("editor"))
    ],
    db: Session = Depends(get_db)
):
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

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
        assignment_to_response(assignment, manuscript)
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
        Depends(require_role("editor"))
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

    # Step 2: Use current review round
    review_round = manuscript.review_round

    # Step 3: Get assignments for current round
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == review_round
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

    # Step 3: Find the confirmed pre-review PDF.
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
            status_code=409,
            detail="No confirmed pre-review PDF is available",
        )

    # Step 4: Ensure the confirmed file matches the approved version.
    submission = db.get(
        InitialEditingSubmission,
        confirmation.submission_id,
    )

    if confirmation.file_path != submission.file_path:
        raise HTTPException(
            status_code=409,
            detail="Confirmed PDF does not match the approved version",
        )

    # Step 5: Resolve and validate the PDF path.
    upload_root = (
        BASE_DIR / "uploads" / "initial_editing"
    ).resolve()

    pdf_path = (
        BASE_DIR / confirmation.file_path
    ).resolve()

    if (
        not pdf_path.is_relative_to(upload_root)
        or not pdf_path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="Confirmed PDF file not found",
        )

    with pdf_path.open("rb") as pdf:
        if pdf.read(5) != b"%PDF-":
            raise HTTPException(
                status_code=409,
                detail="Confirmed file is not a valid PDF",
            )

    # Step 6: Return the confirmed PDF.
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=(
            f"manuscript_{manuscript.manuscript_id}"
            f"_review_round_{assignment.review_round}.pdf"
        ),
    )
