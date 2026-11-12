
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User,
    Manuscript,
    Review,
    ReviewAssignment,
    Notification,
    InitialEditorInvitation,
)
from app.schemas import (
    ReviewCreate,
    ReviewResponse,
    ReviewHistoryResponse
)
from app.routers.users import require_role
from app.reviewer_invitations import may_review


router = APIRouter(
    prefix="/reviews",
    tags=["Reviews"]
)


# Reviewer: Submit a review
@router.post(
    "/",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED
)
def submit_review(
    review_data: ReviewCreate,
    current_user: Annotated[
        User,
        Depends(require_role("reviewer"))
    ],
    db: Session = Depends(get_db)
):
    # Step 1: Find the review assignment
    assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.assignment_id == review_data.assignment_id
        )
    )

    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review assignment not found"
        )

    # Step 2: Check reviewer permission
    if assignment.reviewer_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only review your assigned manuscripts"
        )

    if not may_review(db, assignment):
        raise HTTPException(status_code=403, detail="Accept the review invitation before submitting a review")

    # Step 3: Find the manuscript
    manuscript = db.get(
        Manuscript,
        assignment.manuscript_id
    )

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Step 4: Check manuscript status
    if manuscript.status not in ("ready_for_peer_review", "revised"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not open for peer review",
        )


    # Step 5: Use the manuscript's current review round
    current_round = manuscript.review_round

    if assignment.review_round != current_round:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assignment is not in the current review round"
        )

    # Step 6: Prevent duplicate reviews
    existing_review = db.scalar(
        select(Review).where(
            Review.assignment_id == assignment.assignment_id
        )
    )

    if existing_review is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review already submitted for this assignment"
        )

    # Step 7: Check assignment status
    if assignment.status != "assigned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assignment is not available for review"
        )


    # Step 8: Create the review
    review = Review(
        assignment_id=assignment.assignment_id,
        comments=review_data.comments,
        recommendation=review_data.recommendation
    )

    db.add(review)

    # Step 9: Mark assignment as completed
    assignment.status = "completed"

    # Step 10: Notify the editor who assigned the reviewer
    notification = Notification(
        user_id=assignment.assigned_by,
        manuscript_id=manuscript.manuscript_id,
        notification_type="review_submitted",
        message=(
            f"Reviewer {current_user.name} has submitted a review "
            f"for Manuscript #{manuscript.manuscript_id}: "
            f"{manuscript.title} (Round {current_round})."
        ),
        is_read=False
    )

    db.add(notification)

    # Step 11: Save review, assignment status, and notification together
    db.commit()
    db.refresh(review)

    return review



# Editor: View manuscript review history
@router.get(
    "/manuscript/{manuscript_id}",
    response_model=list[ReviewHistoryResponse],
    summary="Get Manuscript Review History"
)
def get_manuscript_reviews(
    manuscript_id: int,
    current_user: Annotated[
        User,
        Depends(require_role("editor"))
    ],
    db: Session = Depends(get_db)
):
    # Step 1: Find the manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Only an editor assigned to this manuscript can view its reviews.
    accepted_invitation = db.scalar(
        select(InitialEditorInvitation).where(
            InitialEditorInvitation.manuscript_id == manuscript_id,
            InitialEditorInvitation.editor_id == current_user.user_id,
            InitialEditorInvitation.status == "accepted",
        )
    )

    assigned_reviewer = db.scalar(
        select(ReviewAssignment.assignment_id).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.assigned_by == current_user.user_id,
        )
    )

    if accepted_invitation is None and assigned_reviewer is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not an assigned editor for this manuscript",
        )

    # Step 2: Get all reviews and their assignments
    results = db.execute(
        select(Review, ReviewAssignment)
        .join(
            ReviewAssignment,
            Review.assignment_id == ReviewAssignment.assignment_id
        )
        .where(
            ReviewAssignment.manuscript_id == manuscript_id
        )
        .order_by(
            ReviewAssignment.review_round.asc(),
            ReviewAssignment.assignment_id.asc()
        )
    ).all()

    # Step 3: Build review history
    history = []

    for review, assignment in results:
        history.append(
            ReviewHistoryResponse(
                review_id=review.review_id,
                assignment_id=review.assignment_id,
                review_round=assignment.review_round,
                reviewer_id=assignment.reviewer_id,
                comments=review.comments,
                recommendation=review.recommendation,
                submitted_at=review.submitted_at
            )
        )

    return history
