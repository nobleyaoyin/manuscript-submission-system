
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Manuscript, Review, ReviewAssignment
from app.schemas import (
    ReviewCreate,
    ReviewResponse,
    ReviewHistoryResponse
)
from app.routers.users import require_role


router = APIRouter(
    prefix="/reviews",
    tags=["Reviews"]
)


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

    # Step 4: Determine the current review round
    if manuscript.status == "submitted":
        current_round = 1
    elif manuscript.status == "revised":
        current_round = 2
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not open for review"
        )

    # Step 5: Validate the review round
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

    # Step 7: Create the review
    review = Review(
        assignment_id=assignment.assignment_id,
        comments=review_data.comments,
        recommendation=review_data.recommendation
    )

    db.add(review)

    # Step 8: Mark assignment as completed
    assignment.status = "completed"

    # Step 9: Save changes
    db.commit()
    db.refresh(review)

    return review



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

    # Step 3: Build the review history response
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
