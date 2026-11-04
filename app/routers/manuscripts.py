from typing import Annotated

from pathlib import Path
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models import Manuscript, User, ReviewAssignment, Review
from app.schemas import ManuscriptCreate, ManuscriptResponse, ManuscriptUpdate, EditorDecision
from app.routers.users import get_current_user, require_role

router = APIRouter(
    prefix="/manuscripts",
    tags=["Manuscripts"]
)


@router.post(
    "/",
    response_model=ManuscriptResponse,
    status_code=status.HTTP_201_CREATED
)
def create_manuscript(
    manuscript_data: ManuscriptCreate,
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db)
):
    # Create a manuscript associated with the logged-in user
    new_manuscript = Manuscript(
        title=manuscript_data.title,
        abstract=manuscript_data.abstract,
        author_id=current_user.user_id,
        status="submitted"
    )

    # Save the manuscript
    db.add(new_manuscript)
    db.commit()
    db.refresh(new_manuscript)

    return new_manuscript


# Editor: View all manuscripts with optional status filtering
@router.get(
    "/",
    response_model=list[ManuscriptResponse],
    summary="Get All Manuscripts"
)
def get_all_manuscripts(
    status: str | None = None,
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db)
):
    # Start with all manuscripts
    query = select(Manuscript)

    # Filter by status if provided
    if status is not None:
        query = query.where(Manuscript.status == status)

    # Sort by manuscript ID
    query = query.order_by(Manuscript.manuscript_id)

    manuscripts = db.scalars(query).all()

    return manuscripts


# Editor: Dashboard statistics by manuscript status
# Keep this static route before /{manuscript_id}.
@router.get("/statistics", summary="Get Manuscript Statistics")
def get_manuscript_statistics(
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db)
):
    statuses = (
        "submitted", "accepted", "rejected",
        "revision_required", "revised"
    )
    counts = {name: 0 for name in statuses}

    # Count all manuscripts, including any unexpected status values.
    manuscripts = db.scalars(select(Manuscript)).all()
    for manuscript in manuscripts:
        if manuscript.status in counts:
            counts[manuscript.status] += 1

    return {"total": len(manuscripts), **counts}



@router.get("/dashboard", summary="Get Editor Dashboard")
def get_editor_dashboard(
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db)
):
    # Step 1: Get all manuscripts
    manuscripts = db.scalars(
        select(Manuscript).order_by(Manuscript.manuscript_id)
    ).all()

    dashboard = []

    for manuscript in manuscripts:
        # Step 2: Determine the review round
        round_query = db.scalar(
            select(ReviewAssignment.review_round)
            .where(
                ReviewAssignment.manuscript_id == manuscript.manuscript_id
            )
            .order_by(ReviewAssignment.review_round.desc())
            .limit(1)
        )

        review_round = round_query or 1

        # Step 3: Get assignments for the current round
        assignments = db.scalars(
            select(ReviewAssignment).where(
                ReviewAssignment.manuscript_id == manuscript.manuscript_id,
                ReviewAssignment.review_round == review_round
            )
        ).all()

        # Step 4: Count completed reviews
        completed_reviews = 0

        for assignment in assignments:
            review = db.scalar(
                select(Review).where(
                    Review.assignment_id == assignment.assignment_id
                )
            )

            if review is not None:
                completed_reviews += 1

        total_reviewers = len(assignments)
        pending_reviews = total_reviewers - completed_reviews

        # Step 5: Determine review status
        if manuscript.status in ["accepted", "rejected"]:
            review_status = "decision_finalized"

        elif manuscript.status == "revision_required":
            review_status = "revision_required"

        elif total_reviewers == 0:
            review_status = "not_assigned"

        elif pending_reviews > 0:
            review_status = "in_progress"

        else:
            review_status = "completed"

        # Step 6: Build dashboard item
        dashboard.append({
            "manuscript_id": manuscript.manuscript_id,
            "title": manuscript.title,
            "status": manuscript.status,
            "review_round": review_round,
            "total_reviewers": total_reviewers,
            "completed_reviews": completed_reviews,
            "pending_reviews": pending_reviews,
            "review_status": review_status
        })

    return dashboard




# Editor: Summary of manuscript review progress
@router.get(
    "/dashboard/summary",
    summary="Get Editor Dashboard Summary"
)
def get_editor_dashboard_summary(
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db)
):
    manuscripts = db.scalars(select(Manuscript)).all()

    summary = {
        "total_manuscripts": len(manuscripts),
        "not_assigned": 0,
        "in_progress": 0,
        "ready_for_decision": 0,
        "revision_required": 0,
        "decision_finalized": 0
    }

    for manuscript in manuscripts:
        if manuscript.status in ("accepted", "rejected"):
            summary["decision_finalized"] += 1
            continue

        if manuscript.status == "revision_required":
            summary["revision_required"] += 1
            continue

        # Review round is based on the manuscript's current workflow state.
        review_round = 2 if manuscript.status == "revised" else 1

        assignments = db.scalars(
            select(ReviewAssignment).where(
                ReviewAssignment.manuscript_id == manuscript.manuscript_id,
                ReviewAssignment.review_round == review_round
            )
        ).all()

        if not assignments:
            summary["not_assigned"] += 1
            continue

        completed_reviews = sum(
            1 for assignment in assignments
            if db.scalar(
                select(Review.review_id).where(
                    Review.assignment_id == assignment.assignment_id
                )
            ) is not None
        )

        if completed_reviews == len(assignments):
            summary["ready_for_decision"] += 1
        else:
            summary["in_progress"] += 1

    return summary


@router.get(
    "/my",
    response_model=list[ManuscriptResponse],
    summary="Get My Manuscripts"
)
def get_my_manuscripts(
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db)
):
    manuscripts = db.scalars(
        select(Manuscript)
        .where(Manuscript.author_id == current_user.user_id)
        .order_by(Manuscript.manuscript_id)
    ).all()

    return manuscripts



@router.get(
    "/{manuscript_id}",
    response_model=ManuscriptResponse
)
def get_manuscript(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    # Find the manuscript by its ID
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    # Check whether the user is the author
    is_author = manuscript.author_id == current_user.user_id

    # Check whether the user is an editor
    is_editor = current_user.role == "editor"

    # Check whether the reviewer is assigned to this manuscript
    is_assigned_reviewer = False

    if current_user.role == "reviewer":
        assignment = db.scalar(
            select(ReviewAssignment).where(
                ReviewAssignment.manuscript_id == manuscript_id,
                ReviewAssignment.reviewer_id == current_user.user_id
            )
        )

        is_assigned_reviewer = assignment is not None

    # Allow only authorized users
    if not (is_author or is_editor or is_assigned_reviewer):
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    return manuscript



@router.patch("/{manuscript_id}", response_model=ManuscriptResponse)
def update_manuscript(
    manuscript_id: int,
    update_data: ManuscriptUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    # Find the manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Only the author can update their own manuscript
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own manuscripts"
        )

    # Only allow editing before the first review assignment
    if manuscript.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript cannot be edited at this stage"
        )

    # Check whether a reviewer has already been assigned
    existing_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id
        )
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript cannot be edited after reviewer assignment"
        )


    # Update only the provided fields
    changes = update_data.model_dump(exclude_unset=True)

    for field, value in changes.items():
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cannot be null"
            )

        setattr(manuscript, field, value)

    # Save changes to the database
    db.commit()
    db.refresh(manuscript)

    return manuscript

@router.delete(
    "/{manuscript_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_manuscript(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    # Find the manuscript
    manuscript = db.scalar(
        select(Manuscript).where(
            Manuscript.manuscript_id == manuscript_id
        )
    )

    # Check whether the manuscript exists
    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Only the author can delete their own manuscript
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own manuscripts"
        )

    # Only submitted manuscripts can be deleted
    if manuscript.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript cannot be deleted at this stage"
        )

    # Prevent deletion after reviewer assignment
    existing_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id
        )
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript cannot be deleted after reviewer assignment"
        )

    # Delete the manuscript from the database
    db.delete(manuscript)
    db.commit()

@router.patch(
    "/{manuscript_id}/decision",
    summary="Editor Decision"
)
def make_editor_decision(
    manuscript_id: int,
    decision_data: EditorDecision,
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db)
):
    # Step 1: Find the manuscript
    manuscript = db.scalar(
        select(Manuscript).where(
            Manuscript.manuscript_id == manuscript_id
        )
    )

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Step 2: Determine the current review round
    if manuscript.status == "submitted":
        current_round = 1
    elif manuscript.status == "revised":
        current_round = 2
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not ready for an editor decision"
        )

    # Step 3: Find assignments for the current review round
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == current_round
        )
    ).all()

    # A manuscript must have at least one reviewer
    if not assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No reviewers have been assigned to this manuscript"
        )

    # Check whether every assigned reviewer has submitted a review
    for assignment in assignments:
        review = db.scalar(
            select(Review).where(
                Review.assignment_id == assignment.assignment_id
            )
        )

        if review is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Not all assigned reviewers have submitted their reviews"
            )

    # Step 2: Update manuscript status
    manuscript.status = decision_data.decision

    # Step 3: Save to database
    db.commit()
    db.refresh(manuscript)

    # Step 4: Return the result
    return {
        "manuscript_id": manuscript.manuscript_id,
        "status": manuscript.status,
        "decided_by": current_user.user_id
    }

@router.post(
    "/{manuscript_id}/resubmit",
    response_model=ManuscriptResponse,
    summary="Resubmit Revised Manuscript"
)
def resubmit_manuscript(
    manuscript_id: int,
    update_data: ManuscriptUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    # Step 1: Find the manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Step 2: Only the author can resubmit
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only resubmit your own manuscripts"
        )

    # Step 3: Only manuscripts requiring revision can be resubmitted
    if manuscript.status != "revision_required":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript does not require revision"
        )

    # Step 4: Update manuscript fields
    changes = update_data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one field to revise"
        )

    for field, value in changes.items():
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cannot be null"
            )

        setattr(manuscript, field, value)

    # Step 5: Change status to revised
    manuscript.status = "revised"

    # Step 6: Save changes
    db.commit()
    db.refresh(manuscript)

    return manuscript



@router.post("/{manuscript_id}/upload", summary="Upload Manuscript PDF")
def upload_manuscript(
    manuscript_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db)
):
    # Step 1: Find the manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    # Step 2: Check ownership
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You can only upload files to your own manuscripts"
        )

    # Step 3: Check manuscript status
    if manuscript.status not in ["submitted", "revision_required"]:
        raise HTTPException(
            status_code=409,
            detail="Manuscript cannot be modified at this stage"
        )

    # Step 4: Prevent uploading after reviewer assignment
    # Only check Round 1 for newly submitted manuscripts.
    # Revision uploads are allowed after Round 1 is completed.
    if manuscript.status == "submitted":
        existing_assignment = db.scalar(
            select(ReviewAssignment).where(
                ReviewAssignment.manuscript_id == manuscript_id,
                ReviewAssignment.review_round == 1
            )
        )

        if existing_assignment is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot upload PDF after reviewer assignment"
            )

    # Step 5: Validate PDF
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed"
        )

    # Step 6: Read file with a 10 MB limit
    max_size = 10 * 1024 * 1024
    content = file.file.read(max_size + 1)

    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail="PDF file exceeds 10 MB limit"
        )

    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file"
        )

    # Step 7: Create a directory for the manuscript
    upload_dir = Path("uploads/manuscripts") / str(manuscript_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Step 8: Save the PDF
    file_path = upload_dir / "paper.pdf"
    file_path.write_bytes(content)

    # Step 9: Save file path to database
    manuscript.file_path = str(file_path)

    db.commit()
    db.refresh(manuscript)

    return {
        "manuscript_id": manuscript.manuscript_id,
        "filename": "paper.pdf",
        "file_path": manuscript.file_path,
        "message": "PDF uploaded successfully"
    }



@router.get(
    "/{manuscript_id}/download",
    summary="Download Manuscript PDF"
)
def download_manuscript(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    # Step 1: Find the manuscript
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    # Step 2: Check download permission
    is_author = manuscript.author_id == current_user.user_id
    is_editor = current_user.role == "editor"

    # Check if the reviewer is assigned to this manuscript
    is_assigned_reviewer = False

    if current_user.role == "reviewer":
        assignment = db.scalar(
            select(ReviewAssignment).where(
                ReviewAssignment.manuscript_id == manuscript_id,
                ReviewAssignment.reviewer_id == current_user.user_id
            )
        )

        is_assigned_reviewer = assignment is not None

    if not (is_author or is_editor or is_assigned_reviewer):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to download this manuscript"
        )

    # Step 3: Check if a PDF has been uploaded
    if manuscript.file_path is None:
        raise HTTPException(
            status_code=404,
            detail="No PDF file has been uploaded for this manuscript"
        )

    # Step 4: Check if the file exists
    file_path = Path(manuscript.file_path)

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="PDF file not found on server"
        )

    # Step 5: Return the PDF file
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=f"manuscript_{manuscript_id}.pdf"
    )
