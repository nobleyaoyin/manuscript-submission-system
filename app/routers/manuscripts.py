from typing import Annotated

from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import os
import tempfile
import io
import zipfile
from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db, BASE_DIR
from app.config import IS_DEMO, guard_demo_path
from app.models import (
    Manuscript,
    User,
    ReviewAssignment,
    Review,
    Notification,
    InitialEditorInvitation,
    ReviewRoundFile,
    ManuscriptReviewPolicy,
    AuthorAgreement,
    FinalEditorInvitation,
    EICInitialScan,
    PreReviewConfirmation,
    EICDecision,
)
from app.schemas import ManuscriptCreate, ManuscriptResponse, ManuscriptUpdate, EditorDecision
from app.routers.users import get_current_user, require_role
from app.reviewer_invitations import not_declined, may_review
from app.review_progress import review_progress

def uses_eic_workflow(db: Session, manuscript_id: int) -> bool:
    """Check whether a manuscript belongs to the EIC-controlled workflow."""
    evidence_models = (
        ManuscriptReviewPolicy,
        EICInitialScan,
        InitialEditorInvitation,
        PreReviewConfirmation,
        EICDecision,
    )

    for model in evidence_models:
        evidence_id = db.scalar(
            select(model.manuscript_id)
            .where(model.manuscript_id == manuscript_id)
            .limit(1)
        )

        if evidence_id is not None:
            return True

    return False

router = APIRouter(
    prefix="/manuscripts",
    tags=["Manuscripts"]
)

# Supported manuscript document formats
ALLOWED_MANUSCRIPT_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

MAX_MANUSCRIPT_FILE_SIZE = 10 * 1024 * 1024


def validate_manuscript_file(filename: str, content: bytes) -> tuple[str, str]:
    """
    Validate a manuscript file and return:
    (extension, media_type).
    """
    extension = Path(filename or "").suffix.lower()

    if extension not in ALLOWED_MANUSCRIPT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOCX, and DOC files are allowed",
        )

    if not content:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty",
        )

    if len(content) > MAX_MANUSCRIPT_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="File exceeds the 10 MB limit",
        )

    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=400,
                detail="Invalid PDF file",
            )

    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())

                if (
                    "[Content_Types].xml" not in names
                    or "word/document.xml" not in names
                ):
                    raise ValueError("Missing DOCX document structure")

                if archive.testzip() is not None:
                    raise ValueError("Corrupted DOCX archive")

        except (zipfile.BadZipFile, ValueError, RuntimeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid DOCX file",
            )

    elif extension == ".doc":
        # Legacy Word documents use the OLE Compound File format.
        ole_signature = bytes.fromhex("D0CF11E0A1B11AE1")

        if not content.startswith(ole_signature):
            raise HTTPException(
                status_code=400,
                detail="Invalid DOC file",
            )

    return extension, ALLOWED_MANUSCRIPT_EXTENSIONS[extension]

# Resolve existing relative paths from the project root.
def resolve_manuscript_file_path(stored_path: str) -> Path:
    path = Path(stored_path)
    if IS_DEMO and path.is_absolute():
        raise HTTPException(status_code=409, detail="Invalid demo file path")
    return guard_demo_path(path if path.is_absolute() else BASE_DIR / path)

def manuscript_has_pdf(manuscript: Manuscript) -> bool:
    """
    Backward-compatible API helper.

    Despite the legacy name, return True when the manuscript
    has a supported PDF or Word document.
    """
    return manuscript_has_file(manuscript)

def manuscript_has_file(manuscript: Manuscript) -> bool:
    """Check whether a supported manuscript document exists."""
    if not manuscript.file_path:
        return False

    file_path = resolve_manuscript_file_path(manuscript.file_path)

    return (
        file_path.is_file()
        and file_path.suffix.lower() in ALLOWED_MANUSCRIPT_EXTENSIONS
    )

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
        final_invitation = db.scalar(select(FinalEditorInvitation.invitation_id).where(
            FinalEditorInvitation.manuscript_id == manuscript.manuscript_id,
            FinalEditorInvitation.editor_id == user.user_id,
            FinalEditorInvitation.status == 'accepted').limit(1))
        if final_invitation is not None:
            return True
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
            select(ReviewAssignment)
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
        assignments = db.scalars(
            select(ReviewAssignment)
            .where(
                ReviewAssignment.manuscript_id
                == manuscript.manuscript_id,
                ReviewAssignment.reviewer_id == user.user_id,
            )
        ).all()

        return any(may_review(db, assignment) for assignment in assignments)

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
    if not manuscript_has_file(manuscript):
        raise HTTPException(
            status_code=409,
            detail="A PDF or Word manuscript file is required before submission",
        )

    manuscript.status = "submitted"
    db.add(ManuscriptReviewPolicy(manuscript_id=manuscript_id, minimum_completed_reviewers=2))
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
                ReviewAssignment.review_round == review_round,
                not_declined(),
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
            "status": manuscript.status,"legacy_editor_decision_allowed": (
                current_user.role == "editor"
                and manuscript.status in ("submitted", "revised")
                and not uses_eic_workflow(db, manuscript.manuscript_id)
            ),
            "has_pdf": manuscript_has_pdf(manuscript),
            "review_round": review_round,
            "decision_progress": review_progress(db, manuscript, review_round),
            "author_agreement_status": db.scalar(select(AuthorAgreement.status).where(AuthorAgreement.manuscript_id == manuscript.manuscript_id).order_by(AuthorAgreement.agreement_id.desc()).limit(1)),
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
                ReviewAssignment.review_round == review_round,
                not_declined(),
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

    # First, verify that the editor can access this manuscript.
    if not can_access_manuscript(db, manuscript, current_user):
        raise HTTPException(
            status_code=403,
            detail="You are not an assigned editor",
        )

    # Prevent the legacy Editor Decision endpoint from bypassing EIC.
    if uses_eic_workflow(db, manuscript_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "This manuscript uses the EIC workflow. "
                "The EIC must make the editorial decision."
            ),
        )
    # Step 2: Decisions are permitted only while a round is under review.
    if manuscript.status not in ("submitted", "revised"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript is not ready for an editor decision"
        )
    current_round = get_current_review_round(db, manuscript)

    # Prevent the older editor endpoint from bypassing the prospective policy.
    progress = review_progress(db, manuscript, current_round)
    if progress['policy'] == 'two_reviewers' and not progress['can_decide']:
        raise HTTPException(status_code=409, detail=progress['blocked_reason'])

    # Step 3: Find assignments for the current review round
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == current_round,
            not_declined(),
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

    upload_dir = guard_demo_path(BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id))

    current_pdf = (
        resolve_manuscript_file_path(manuscript.file_path)
        if manuscript.file_path
        else None
    )

    if (
        current_pdf is None
        or not current_pdf.is_file()
        or current_pdf.parent.resolve() != upload_dir.resolve()
        or current_pdf.suffix.lower() not in ALLOWED_MANUSCRIPT_EXTENSIONS
        or not current_pdf.name.startswith(f"round_{next_round}_")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload a revised PDF or Word file before resubmitting"
        )

    try:
        with current_pdf.open("rb") as revised_file:
            revised_content = revised_file.read(MAX_MANUSCRIPT_FILE_SIZE + 1)
    except OSError:
        raise HTTPException(status_code=409, detail="Revised manuscript file is unavailable")
    validate_manuscript_file(current_pdf.name, revised_content)

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
    existing_round_file = db.scalar(select(ReviewRoundFile).where(
        ReviewRoundFile.manuscript_id == manuscript_id,
        ReviewRoundFile.review_round == next_round,
    ))
    if existing_round_file is not None:
        raise HTTPException(status_code=409, detail="A file is already recorded for this review round")
    db.add(ReviewRoundFile(
        manuscript_id=manuscript_id,
        review_round=next_round,
        source_kind="author_revision",
        confirmation_id=None,
        file_path=manuscript.file_path,
    ))
    manuscript.review_round = next_round
    manuscript.status = "revised"

    # Step 6: Save changes
    db.commit()
    db.refresh(manuscript)

    return manuscript_to_response(manuscript)



@router.post("/{manuscript_id}/upload", summary="Upload Manuscript File")
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


    # Step 5: Read the uploaded document (maximum 10 MB)
    content = file.file.read(MAX_MANUSCRIPT_FILE_SIZE + 1)

    # Step 6: Validate PDF, DOCX, or DOC
    extension, media_type = validate_manuscript_file(
        file.filename,
        content,
    )

    # Each upload gets a unique filename. Older files remain untouched.
    # Existing legacy uploads/paper.pdf files are also preserved.
    upload_dir = guard_demo_path(BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id))
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    filename = f"round_{review_round}_{timestamp}_{uuid4().hex[:8]}{extension}"

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
        raise HTTPException(status_code=500, detail="Could not save manuscript file")

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
        "message": "Manuscript file uploaded successfully"
    }



@router.get(
    "/{manuscript_id}/download",
    summary="Download Manuscript File"
)
def download_manuscript(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    if current_user.role == "reviewer":
        raise HTTPException(status_code=403, detail="Use your review assignment to download the assigned file")
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


    # Step 5: Return the manuscript in its original format
    extension = file_path.suffix.lower()

    media_type = ALLOWED_MANUSCRIPT_EXTENSIONS.get(extension)

    if media_type is None:
        raise HTTPException(
            status_code=400,
            detail="Unsupported manuscript file format",
        )

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=f"manuscript_{manuscript_id}{extension}",
    )

# List all saved PDF uploads for a manuscript, including legacy paper.pdf.
@router.get("/{manuscript_id}/versions", summary="List Manuscript File Versions")
def list_manuscript_versions(
    manuscript_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    if current_user.role == "reviewer":
        raise HTTPException(status_code=403, detail="Reviewers cannot access manuscript version history")
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

    upload_dir = guard_demo_path(BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id))
    versions = []
    if upload_dir.is_dir():

        for path in upload_dir.iterdir():
            guard_demo_path(path)
            if (
                not path.is_file()
                or path.suffix.lower() not in ALLOWED_MANUSCRIPT_EXTENSIONS
            ):
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
    summary="Download Historical Manuscript File"
)
def download_manuscript_version(
    manuscript_id: int,
    filename: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db)
):
    if current_user.role == "reviewer":
        raise HTTPException(status_code=403, detail="Reviewers cannot access manuscript version history")
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
        or Path(filename).suffix.lower() not in ALLOWED_MANUSCRIPT_EXTENSIONS
        or filename in (".", "..")
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid manuscript filename",
        )


    upload_dir = guard_demo_path(BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)).resolve()
    file_path = guard_demo_path(upload_dir / filename).resolve()
    if file_path.parent != upload_dir or not file_path.is_file():
        raise HTTPException(status_code=404, detail="PDF version not found")

    return FileResponse(
        path=file_path,
        media_type=ALLOWED_MANUSCRIPT_EXTENSIONS[
            file_path.suffix.lower()
        ],
        filename=filename,
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

    # Verify that the author uploaded a new manuscript file after the
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

    if not manuscript_has_file(manuscript):
        raise HTTPException(
            status_code=409,
            detail="Upload a revised PDF or Word file before resubmitting",
        )

    current_file = resolve_manuscript_file_path(manuscript.file_path)

    # Uploaded manuscript files use UTC timestamps in their filenames.
    # The file must belong to this manuscript and be newer
    # than the latest EIC revision request.
    upload_dir = guard_demo_path(
        BASE_DIR / "uploads" / "manuscripts" / str(manuscript_id)
    ).resolve()

    if current_file.parent.resolve() != upload_dir:
        raise HTTPException(
            status_code=409,
            detail="Upload a revised PDF or Word file before resubmitting",
        )

    try:
        timestamp_text = current_file.name.split("_")[2]
        upload_time = datetime.strptime(
            timestamp_text,
            "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc)

        scan_time = latest_scan.created_at

        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)

        if upload_time <= scan_time:
            raise ValueError("Manuscript file predates the EIC revision request")

    except (ValueError, IndexError):
        raise HTTPException(
            status_code=409,
            detail="Upload a new revised PDF or Word file before resubmitting",
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
