from typing import Annotated

from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import os
import tempfile
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db, BASE_DIR
from app.models import (
    Manuscript,
    User,
    ReviewAssignment,
    Review,
    Notification,
    InitialEditorInvitation,
)
from app.schemas import ManuscriptCreate, ManuscriptResponse, ManuscriptUpdate, EditorDecision
from app.routers.users import get_current_user, require_role

router = APIRouter(
    prefix="/manuscripts",
    tags=["Manuscripts"]
)



# Resolve existing relative paths from the project root.
def resolve_manuscript_file_path(stored_path: str) -> Path:
    path = Path(stored_path)
    return path if path.is_absolute() else BASE_DIR / path


def manuscript_has_pdf(manuscript: Manuscript) -> bool:
    """True only when the current manuscript PDF exists on disk."""
    if not manuscript.file_path:
        return False
    return resolve_manuscript_file_path(manuscript.file_path).is_file()


def manuscript_to_response(manuscript: Manuscript) -> ManuscriptResponse:
    """Serialize a manuscript with its computed PDF availability."""
    response = ManuscriptResponse.model_validate(manuscript)
    return response.model_copy(update={"has_pdf": manuscript_has_pdf(manuscript)})


def get_current_review_round(db: Session, manuscript: Manuscript) -> int:
    """Read the persisted round; older records fall back to assignment history."""
    stored_round = getattr(manuscript, "review_round", None)
    if isinstance(stored_round, int) and stored_round >= 1:
        return stored_round

    latest_round = db.scalar(
        select(ReviewAssignment.review_round)
        .where(ReviewAssignment.manuscript_id == manuscript.manuscript_id)
        .order_by(ReviewAssignment.review_round.desc())
        .limit(1)
    )
    return latest_round or 1



def can_access_manuscript(
    db: Session,
    manuscript: Manuscript,
    user: User,
) -> bool:
    # Authors can access their own manuscripts.
    if manuscript.author_id == user.user_id:
        return True

    # Drafts are private to their authors.
    if manuscript.status == "draft":
        return False

    # EIC can access submitted manuscripts.
    if user.role == "eic":
        return True

    # Editors need an accepted initial editor invitation,
    # or an existing peer-review assignment made by them.
    if user.role == "editor":
        invitation = db.scalar(
            select(InitialEditorInvitation.invitation_id)
            .where(
                InitialEditorInvitation.manuscript_id
                == manuscript.manuscript_id,
                InitialEditorInvitation.editor_id == user.user_id,
                InitialEditorInvitation.status == "accepted",
            )
            .limit(1)
        )

        if invitation is not None:
            return True

        assignment = db.scalar(
            select(ReviewAssignment.assignment_id)
            .where(
                ReviewAssignment.manuscript_id
                == manuscript.manuscript_id,
                ReviewAssignment.assigned_by == user.user_id,
            )
            .limit(1)
        )

        return assignment is not None

    # Reviewers need an assignment.
    if user.role == "reviewer":
        assignment = db.scalar(
            select(ReviewAssignment.assignment_id)
            .where(
                ReviewAssignment.manuscript_id
                == manuscript.manuscript_id,
                ReviewAssignment.reviewer_id == user.user_id,
            )
            .limit(1)
        )

        return assignment is not None

    return False



def can_access_manuscript_pdf(
    db: Session,
    manuscript: Manuscript,
    user: User,
) -> bool:
    # Authors can always access their own PDFs.
    if manuscript.author_id == user.user_id:
        return True

    # Unsubmitted PDFs remain private to the author.
    if manuscript.status in (
        "draft",
        "revision_required",
        "initial_revision_required",
    ):
        return False

    return can_access_manuscript(db, manuscript, user)

def get_accessible_manuscripts(
    db: Session,
    user: User,
) -> list[Manuscript]:
    """
    Return non-draft manuscripts accessible to an EIC or Editor.
    """

    if user.role not in ("eic", "editor"):
        return []

    manuscripts = db.scalars(
        select(Manuscript)
        .where(Manuscript.status != "draft")
        .order_by(Manuscript.manuscript_id)
    ).all()

    # EIC can access all submitted manuscripts.
    if user.role == "eic":
        return list(manuscripts)

    # Editors can only access manuscripts they are authorized for.
    return [
        manuscript
        for manuscript in manuscripts
        if can_access_manuscript(db, manuscript, user)
    ]

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
        status="draft",
        review_round=1
    )

    # Save the manuscript
    db.add(new_manuscript)
    db.commit()
    db.refresh(new_manuscript)

    return manuscript_to_response(new_manuscript)


@router.post(
    "/{manuscript_id}/submit",
    response_model=ManuscriptResponse,
    summary="Submit Draft Manuscript"
)
def submit_draft_manuscript(
    manuscript_id: int,
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db)
):
    manuscript = db.get(Manuscript, manuscript_id)
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="You can only submit your own drafts")
    if manuscript.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft manuscripts can be submitted")
    if not manuscript.title.strip() or not manuscript.abstract.strip():
        raise HTTPException(status_code=409, detail="Title and abstract are required")
    if not manuscript_has_pdf(manuscript):
        raise HTTPException(status_code=409, detail="A PDF is required before submission")

    manuscript.status = "submitted"
    db.commit()
    db.refresh(manuscript)
    return manuscript_to_response(manuscript)


# Editor: View all manuscripts with optional status filtering
@router.get(
    "/",
    response_model=list[ManuscriptResponse],
    summary="Get Accessible Manuscripts",
)
def get_all_manuscripts(
    status: str | None = None,
    current_user: User = Depends(require_role("editor", "eic")),
    db: Session = Depends(get_db),
):
    # Get only manuscripts the current user can access.
    manuscripts = get_accessible_manuscripts(
        db,
        current_user,
    )

    # Apply optional status filtering.
    if status is not None:
        manuscripts = [
            manuscript
            for manuscript in manuscripts
            if manuscript.status == status
        ]

    return [
        manuscript_to_response(manuscript)
        for manuscript in manuscripts
    ]


# Editor: Dashboard statistics by manuscript status
# Keep this static route before /{manuscript_id}.

@router.get(
    "/statistics",
    summary="Get Manuscript Statistics",
)
def get_manuscript_statistics(
    current_user: User = Depends(require_role("editor", "eic")),
    db: Session = Depends(get_db),
):
    # Get only manuscripts accessible to the current user.
    manuscripts = get_accessible_manuscripts(
        db,
        current_user,
    )

    # Count manuscripts by workflow status.
    counts = {
        "submitted": 0,
        "accepted": 0,
        "rejected": 0,
        "revision_required": 0,
        "revised": 0,
        "initial_scan_passed": 0,
        "initial_revision_required": 0,
        "initial_rejected": 0,
        "initial_editor_invited": 0,
        "initial_editing": 0,
    }

    for manuscript in manuscripts:
        if manuscript.status in counts:
            counts[manuscript.status] += 1

    return {
        "total": len(manuscripts),
        **counts,
    }

@router.get("/dashboard", summary="Get Editor Dashboard")
def get_editor_dashboard(
    current_user: User = Depends(require_role("editor", "eic")),
    db: Session = Depends(get_db)
):
    # Step 1: Get only accessible manuscripts
    manuscripts = get_accessible_manuscripts(
        db,
        current_user,
    )

    dashboard = []

    for manuscript in manuscripts:
        # Step 2: Determine the review round
        review_round = get_current_review_round(db, manuscript)

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
            "has_pdf": manuscript_has_pdf(manuscript),
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
    current_user: User = Depends(require_role("editor", "eic")),
    db: Session = Depends(get_db)
):
    manuscripts = get_accessible_manuscripts(
        db,
        current_user,
    )

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

        # Use the same round calculation as the editor dashboard.
        review_round = get_current_review_round(db, manuscript)

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

    return [manuscript_to_response(item) for item in manuscripts]

@router.get(
    "/{manuscript_id}",
    response_model=ManuscriptResponse,
    summary="Get Manuscript Details",
)
def get_manuscript(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Use the centralized manuscript access rules.
    if not can_access_manuscript(db, manuscript, current_user):
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    return manuscript_to_response(manuscript)




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
    if manuscript.status not in ("draft", "submitted"):
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

    return manuscript_to_response(manuscript)

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

    # Only draft and unassigned legacy submitted manuscripts can be deleted
    if manuscript.status not in ("draft", "submitted"):
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

    # Step 2: Decisions are permitted only while a round is under review.
    if manuscript.status not in ("submitted", "revised"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not ready for an editor decision"
        )
    current_round = get_current_review_round(db, manuscript)

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

    # Step 4: Update manuscript status
    manuscript.status = decision_data.decision

    # Step 5: Create notification for the author
    decision_messages = {
        "accepted": "has been accepted",
        "rejected": "has been rejected",
        "revision_required": "requires revision",
    }

    decision_text = decision_messages.get(
        decision_data.decision,
        decision_data.decision
    )

    notification = Notification(
        user_id=manuscript.author_id,
        manuscript_id=manuscript.manuscript_id,
        notification_type="editor_decision",
        message=(
            f"Your manuscript #{manuscript.manuscript_id}: "
            f"{manuscript.title} {decision_text} "
            f"(Round {current_round})."
        ),
        is_read=False
    )

    db.add(notification)

    # Step 6: Save changes
    db.commit()
    db.refresh(manuscript)

    # Step 7: Return result
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

    # Require a new PDF for the upcoming review round.
    next_round = get_current_review_round(db, manuscript) + 1

    upload_dir = BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)

    current_pdf = (
        resolve_manuscript_file_path(manuscript.file_path)
        if manuscript.file_path
        else None
    )

    if (
        current_pdf is None
        or not current_pdf.is_file()
        or current_pdf.parent.resolve() != upload_dir.resolve()
        or not current_pdf.name.startswith(f"round_{next_round}_")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload a revised PDF before resubmitting"
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

    # Step 5: Advance exactly once when the author formally resubmits.
    # PDF uploads for revisions are saved under this upcoming round.
    manuscript.review_round = next_round
    manuscript.status = "revised"

    # Step 6: Save changes
    db.commit()
    db.refresh(manuscript)

    return manuscript_to_response(manuscript)



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

    # Step 3: PDFs can be changed only before formal submission or while
    # preparing a revision requested by the editor.
    if manuscript.status not in (
            "draft",
            "revision_required",
            "initial_revision_required",
    ):
        raise HTTPException(
            status_code=409,
            detail="PDF uploads are only allowed for drafts or manuscripts requiring revision"
        )

    # Step 4: Prevent uploading after reviewer assignment in the target round.
    # For a revision, upload the PDF for the *next* review round.
    review_round = (
        get_current_review_round(db, manuscript) + 1
        if manuscript.status == "revision_required"
        else get_current_review_round(db, manuscript)
    )

    existing_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == review_round
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

    # Each upload gets a unique filename. Older files remain untouched.
    # Existing legacy uploads/paper.pdf files are also preserved.
    upload_dir = BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"round_{review_round}_{timestamp}_{uuid4().hex[:8]}.pdf"
    file_path = upload_dir / filename

    # Write to a temporary file, then atomically publish the completed PDF.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=upload_dir, suffix=".tmp", delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        os.replace(temp_path, file_path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not save PDF")

    previous_path = manuscript.file_path
    manuscript.file_path = str(file_path.relative_to(BASE_DIR))

    try:
        db.commit()
        db.refresh(manuscript)
    except Exception:
        db.rollback()
        manuscript.file_path = previous_path
        file_path.unlink(missing_ok=True)
        raise

    return {
        "manuscript_id": manuscript.manuscript_id,
        "filename": filename,
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

    # Step 2: Check PDF download permission
    if not can_access_manuscript_pdf(db, manuscript, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to download this PDF",
        )

    # Step 3: Check if a PDF has been uploaded
    if manuscript.file_path is None:
        raise HTTPException(
            status_code=404,
            detail="No PDF file has been uploaded for this manuscript"
        )

    # Step 4: Check if the file exists
    file_path = resolve_manuscript_file_path(manuscript.file_path)

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



# List all saved PDF uploads for a manuscript, including legacy paper.pdf.
@router.get("/{manuscript_id}/versions", summary="List Manuscript PDF Versions")
def list_manuscript_versions(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    if not can_access_manuscript_pdf(db, manuscript, current_user):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view PDF versions"
        )

    upload_dir = BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)
    versions = []
    if upload_dir.is_dir():
        for path in upload_dir.glob("*.pdf"):
            if not path.is_file():
                continue
            versions.append({
                "filename": path.name,
                "is_current": (
                    manuscript.file_path is not None
                    and resolve_manuscript_file_path(manuscript.file_path).resolve()
                    == path.resolve()
                ),
                "size_bytes": path.stat().st_size
            })

    return sorted(versions, key=lambda item: item["filename"])


@router.get(
    "/{manuscript_id}/versions/{filename}",
    summary="Download Historical Manuscript PDF"
)
def download_manuscript_version(
    manuscript_id: int,
    filename: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found"
        )

    if not can_access_manuscript_pdf(db, manuscript, current_user):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to download this PDF version"
        )

    # Only allow a single basename within this manuscript's upload folder.
    if (
        Path(filename).name != filename
        or not filename.endswith(".pdf")
        or filename in (".", "..")
    ):
        raise HTTPException(status_code=400, detail="Invalid PDF filename")

    upload_dir = (BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)).resolve()
    file_path = (upload_dir / filename).resolve()
    if file_path.parent != upload_dir or not file_path.is_file():
        raise HTTPException(status_code=404, detail="PDF version not found")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=filename
    )

@router.post(
    "/{manuscript_id}/initial-resubmit",
    response_model=ManuscriptResponse,
    summary="Resubmit Manuscript for EIC Initial Scan",
)
def initial_resubmit_manuscript(
    manuscript_id: int,
    update_data: ManuscriptUpdate,
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db),
):
    # Find the manuscript.
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Only the original author can resubmit.
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You can only resubmit your own manuscript",
        )

    # Only manuscripts awaiting initial revision are eligible.
    if manuscript.status != "initial_revision_required":
        raise HTTPException(
            status_code=409,
            detail="Manuscript does not require initial revision",
        )

    # Initial revision must happen before peer review.
    existing_assignment = db.scalar(
        select(ReviewAssignment.assignment_id)
        .where(ReviewAssignment.manuscript_id == manuscript_id)
        .limit(1)
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=409,
            detail="Peer review has already started",
        )

    # Verify that the author uploaded a new PDF after the
    # most recent EIC initial scan.
    from app.models import EICInitialScan

    latest_scan = db.scalar(
        select(EICInitialScan)
        .where(EICInitialScan.manuscript_id == manuscript_id)
        .order_by(
            EICInitialScan.created_at.desc(),
            EICInitialScan.scan_id.desc(),
        )
        .limit(1)
    )

    if latest_scan is None or latest_scan.decision != "revision_required":
        raise HTTPException(
            status_code=409,
            detail="No pending EIC revision request",
        )

    if not manuscript_has_pdf(manuscript):
        raise HTTPException(
            status_code=409,
            detail="Upload a revised PDF before resubmitting",
        )

    current_pdf = resolve_manuscript_file_path(manuscript.file_path)

    # Uploaded PDFs use UTC timestamps in their filenames.
    # The PDF must belong to this manuscript and be newer
    # than the latest EIC revision request.
    upload_dir = (
        BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)
    ).resolve()

    if current_pdf.parent.resolve() != upload_dir:
        raise HTTPException(
            status_code=409,
            detail="Upload a revised PDF before resubmitting",
        )

    try:
        timestamp_text = current_pdf.name.split("_")[2]
        upload_time = datetime.strptime(
            timestamp_text,
            "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc)

        scan_time = latest_scan.created_at

        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)

        if upload_time <= scan_time:
            raise ValueError("PDF predates the EIC revision request")

    except (ValueError, IndexError):
        raise HTTPException(
            status_code=409,
            detail="Upload a new revised PDF before resubmitting",
        )

    # Update optional manuscript metadata.
    changes = update_data.model_dump(exclude_unset=True)

    for field, value in changes.items():
        if value is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )

        setattr(manuscript, field, value)

    # Return to EIC screening without advancing review_round.
    manuscript.status = "submitted"

    # Notify the EIC who requested the revision.
    db.add(
        Notification(
            user_id=latest_scan.eic_id,
            manuscript_id=manuscript_id,
            notification_type="initial_resubmission",
            message=(
                f"Manuscript #{manuscript_id} has been "
                "resubmitted for EIC initial screening."
            ),
        )
    )

    db.commit()
    db.refresh(manuscript)

    return manuscript_to_response(manuscript)
