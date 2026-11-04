
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Manuscript, ReviewAssignment
from app.schemas import (
    ReviewAssignmentCreate,
    ReviewAssignmentResponse,
)
from app.routers.users import require_role


router = APIRouter(
    prefix="/assignments",
    tags=["Review Assignments"]
)


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

    # Step 2: Validate manuscript status and review round
    if manuscript.status == "submitted":
        expected_round = 1
    elif manuscript.status == "revised":
        expected_round = 2
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not available for reviewer assignment"
        )

    if assignment_data.review_round != expected_round:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This manuscript requires review round {expected_round}"
        )

    # Step 3: Find the reviewer
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

    # Step 4: Prevent duplicate assignments in the same round
    existing_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == assignment_data.manuscript_id,
            ReviewAssignment.reviewer_id == assignment_data.reviewer_id,
            ReviewAssignment.review_round == assignment_data.review_round
        )
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reviewer already assigned in this review round"
        )

    # Step 5: Create the assignment
    new_assignment = ReviewAssignment(
        manuscript_id=assignment_data.manuscript_id,
        reviewer_id=assignment_data.reviewer_id,
        assigned_by=current_user.user_id,
        review_round=assignment_data.review_round,
        status="assigned"
    )

    db.add(new_assignment)
    db.commit()
    db.refresh(new_assignment)

    return new_assignment


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

    return assignments


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
        select(ReviewAssignment).where(
            ReviewAssignment.reviewer_id == current_user.user_id
        ).order_by(
            ReviewAssignment.assignment_id
        )
    ).all()

    return assignments


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
    # Step 1: Check whether the manuscript exists
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Step 2: Find all review assignments for this manuscript
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

    # Step 3: Return the review progress
    return assignments


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
    # Step 1: Check whether the manuscript exists
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Step 2: Determine the current review round
    if manuscript.status == "revised":
        review_round = 2
    else:
        # Use the latest existing review round
        latest_assignment = db.scalar(
            select(ReviewAssignment)
            .where(
                ReviewAssignment.manuscript_id == manuscript_id
            )
            .order_by(
                ReviewAssignment.review_round.desc()
            )
        )

        review_round = (
            latest_assignment.review_round
            if latest_assignment is not None
            else 1
        )

    # Step 3: Get assignments for the current round
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == review_round
        )
    ).all()

    # Step 4: Count review progress
    total_reviewers = len(assignments)

    completed_reviews = sum(
        1 for assignment in assignments
        if assignment.status == "completed"
    )

    pending_reviews = total_reviewers - completed_reviews

    # Step 5: Check whether all reviews are completed
    all_reviews_completed = (
        total_reviewers > 0
        and pending_reviews == 0
    )

    # Step 6: Return summary
    return {
        "manuscript_id": manuscript_id,
        "review_round": review_round,
        "total_reviewers": total_reviewers,
        "completed_reviews": completed_reviews,
        "pending_reviews": pending_reviews,
        "all_reviews_completed": all_reviews_completed
    }
