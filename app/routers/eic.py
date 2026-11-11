
import os
import tempfile
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db

from app.models import (
    User,
    Manuscript,
    EICInitialScan,
    Notification,
    ReviewAssignment,
    InitialEditorInvitation,
    InitialEditingSubmission,
    PreReviewConfirmation,
    Review,
    EICDecision,
    FinalEditingSubmission,
)

from app.schemas import (
    EICInitialScanCreate,
    InitialEditorInviteCreate,
    InitialEditorInvitationResponse,
    InitialEditorInvitationDecision,
    InitialEditingAuthorReview,
    InitialEditingEditorApproval,
    InitialEditingEICApproval,
    PreReviewConfirmationRequest,
    EICDecisionCreate,
)
from app.routers.users import require_role

BASE_DIR = Path(__file__).resolve().parents[2]
router = APIRouter(
    prefix="/eic",
    tags=["EIC"],
)


@router.post(
    "/manuscripts/{manuscript_id}/initial-scan",
    status_code=status.HTTP_201_CREATED,
    summary="Perform EIC Initial Scan",
)
def perform_initial_scan(
    manuscript_id: int,
    scan_data: EICInitialScanCreate,
    current_user: User = Depends(require_role("eic")),
    db: Session = Depends(get_db),
):
    # Find the manuscript.
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Initial screening is only allowed for submitted manuscripts.
    if manuscript.status != "submitted":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not awaiting initial screening",
        )

    # Require a submitted PDF.
    from app.routers.manuscripts import manuscript_has_pdf

    if not manuscript_has_pdf(manuscript):
        raise HTTPException(
            status_code=409,
            detail="Manuscript PDF is missing",
        )

    # Prevent initial screening after peer review has started.
    existing_assignment = db.scalar(
        select(ReviewAssignment.assignment_id)
        .where(
            ReviewAssignment.manuscript_id == manuscript_id
        )
        .limit(1)
    )

    if existing_assignment is not None:
        raise HTTPException(
            status_code=409,
            detail="Peer review has already started",
        )

    # Map EIC decisions to separate workflow statuses.
    status_map = {
        "pass": "initial_scan_passed",
        "revision_required": "initial_revision_required",
        "reject": "initial_rejected",
    }

    new_status = status_map[scan_data.decision]

    # Save the initial screening history.
    scan = EICInitialScan(
        manuscript_id=manuscript_id,
        eic_id=current_user.user_id,
        decision=scan_data.decision,
        comments=scan_data.comments,
    )

    db.add(scan)

    # Update manuscript workflow status.
    manuscript.status = new_status

    # Notify the author.
    notification = Notification(
        user_id=manuscript.author_id,
        manuscript_id=manuscript_id,
        notification_type="eic_initial_scan",
        message=(
            f"EIC initial screening: {scan_data.decision}. "
            f"Comments: {scan_data.comments}"
        ),
    )

    db.add(notification)

    # Save all changes in one transaction.
    db.commit()
    db.refresh(scan)

    return {
        "scan_id": scan.scan_id,
        "manuscript_id": manuscript_id,
        "eic_id": current_user.user_id,
        "decision": scan.decision,
        "comments": scan.comments,
        "manuscript_status": manuscript.status,
        "created_at": scan.created_at,
    }


@router.post(
    "/initial-editor/invite",
    response_model=InitialEditorInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite an Initial Editor",
)
def invite_initial_editor(
    invitation_data: InitialEditorInviteCreate,
    current_user: User = Depends(require_role("eic")),
    db: Session = Depends(get_db),
):
    manuscript = db.get(
        Manuscript,
        invitation_data.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Only manuscripts that passed the EIC initial scan
    # can proceed to initial editing.
    if manuscript.status != "initial_scan_passed":
        raise HTTPException(
            status_code=409,
            detail="Manuscript has not passed initial screening",
        )

    # Verify the selected user is an editor.
    editor = db.get(User, invitation_data.editor_id)

    if editor is None or editor.role != "editor":
        raise HTTPException(
            status_code=400,
            detail="Selected user is not an editor",
        )

    # Prevent another active invitation or assignment.
    existing_invitation = db.scalar(
        select(InitialEditorInvitation)
        .where(
            InitialEditorInvitation.manuscript_id
            == manuscript.manuscript_id,
            InitialEditorInvitation.status.in_(
                ["pending", "accepted"]
            ),
        )
        .limit(1)
    )

    if existing_invitation is not None:
        raise HTTPException(
            status_code=409,
            detail="An active editor invitation already exists",
        )

    # Create invitation.
    invitation = InitialEditorInvitation(
        manuscript_id=manuscript.manuscript_id,
        editor_id=editor.user_id,
        invited_by=current_user.user_id,
        status="pending",
    )

    db.add(invitation)

    # Update manuscript workflow status.
    manuscript.status = "initial_editor_invited"

    # Notify the invited editor.
    db.add(
        Notification(
            user_id=editor.user_id,
            manuscript_id=manuscript.manuscript_id,
            notification_type="initial_editor_invitation",
            message=(
                f"You have been invited by the EIC to edit "
                f"manuscript #{manuscript.manuscript_id}: "
                f"{manuscript.title}"
            ),
        )
    )

    db.commit()
    db.refresh(invitation)

    return invitation


@router.post(
    "/initial-editor/invitations/{invitation_id}/respond",
    response_model=InitialEditorInvitationResponse,
    summary="Accept or Decline Initial Editor Invitation",
)
def respond_initial_editor_invitation(
    invitation_id: int,
    response_data: InitialEditorInvitationDecision,
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    invitation = db.get(
        InitialEditorInvitation,
        invitation_id,
    )

    if invitation is None:
        raise HTTPException(
            status_code=404,
            detail="Invitation not found",
        )

    # Only the invited editor can respond.
    if invitation.editor_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You are not the invited editor",
        )

    if invitation.status != "pending":
        raise HTTPException(
            status_code=409,
            detail="Invitation has already been answered",
        )

    manuscript = db.get(
        Manuscript,
        invitation.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    if manuscript.status != "initial_editor_invited":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not awaiting an editor response",
        )

    # Update invitation and manuscript statuses.
    if response_data.decision == "accept":
        invitation.status = "accepted"
        manuscript.status = "initial_editing"
    else:
        invitation.status = "declined"
        manuscript.status = "initial_scan_passed"

    invitation.responded_at = datetime.now(timezone.utc)

    # Notify the EIC who sent the invitation.
    db.add(
        Notification(
            user_id=invitation.invited_by,
            manuscript_id=manuscript.manuscript_id,
            notification_type="initial_editor_response",
            message=(
                f"Editor {current_user.name} has "
                f"{invitation.status} the invitation "
                f"for manuscript #{manuscript.manuscript_id}."
            ),
        )
    )

    db.commit()
    db.refresh(invitation)

    return invitation

@router.post(
    "/initial-editing/{manuscript_id}/submit",
    status_code=status.HTTP_201_CREATED,
    summary="Submit Initial Editing PDF",
)
def submit_initial_editing(
    manuscript_id: int,
    file: UploadFile = File(...),
    comments: str = Form(...),
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the manuscript.
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 2: Only allow submissions during initial editing.
    if manuscript.status != "initial_editing":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not in initial editing",
        )

    # Step 3: Verify that this editor accepted the invitation.
    invitation = db.scalar(
        select(InitialEditorInvitation).where(
            InitialEditorInvitation.manuscript_id == manuscript_id,
            InitialEditorInvitation.editor_id == current_user.user_id,
            InitialEditorInvitation.status == "accepted",
        )
    )

    if invitation is None:
        raise HTTPException(
            status_code=403,
            detail="You are not the assigned initial editor",
        )

    # Step 4: Prevent multiple pending submissions.
    pending_submission = db.scalar(
        select(InitialEditingSubmission).where(
            InitialEditingSubmission.manuscript_id == manuscript_id,
            InitialEditingSubmission.status == "pending_author",
        )
    )

    if pending_submission is not None:
        raise HTTPException(
            status_code=409,
            detail="An editing submission is already awaiting author review",
        )

    # Step 5: Validate comments.
    comments = comments.strip()

    if not comments or len(comments) > 5000:
        raise HTTPException(
            status_code=400,
            detail="Comments must contain 1 to 5000 characters",
        )

    # Step 6: Validate the PDF.
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed",
        )

    max_size = 10 * 1024 * 1024
    content = file.file.read(max_size + 1)

    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail="PDF file exceeds 10 MB limit",
        )

    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file",
        )

    # Step 7: Save the edited PDF separately from the original.
    upload_dir = (
        BASE_DIR / "uploads" / "initial_editing" / str(manuscript_id)
    )
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    filename = (
        f"editor_{current_user.user_id}_"
        f"{timestamp}_{uuid4().hex[:8]}.pdf"
    )
    file_path = upload_dir / filename

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=upload_dir,
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)

        os.replace(temp_path, file_path)

    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail="Could not save edited PDF",
        )

    # Step 8: Create an editing submission record.
    submission = InitialEditingSubmission(
        manuscript_id=manuscript_id,
        editor_id=current_user.user_id,
        file_path=str(file_path.relative_to(BASE_DIR)),
        comments=comments,
        status="pending_author",
    )

    db.add(submission)

    # Step 9: Notify the author.
    db.add(
        Notification(
            user_id=manuscript.author_id,
            manuscript_id=manuscript_id,
            notification_type="initial_editing_submitted",
            message=(
                f"Editor {current_user.name} submitted an edited "
                f"PDF for manuscript #{manuscript_id}. "
                f"Comments: {comments}"
            ),
        )
    )

    # Step 10: Save the database changes.
    try:
        db.commit()
        db.refresh(submission)

    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    return {
        "submission_id": submission.submission_id,
        "manuscript_id": manuscript_id,
        "editor_id": current_user.user_id,
        "filename": filename,
        "file_path": submission.file_path,
        "comments": submission.comments,
        "status": submission.status,
        "created_at": submission.created_at,
        "message": "Initial editing PDF submitted successfully",
    }

@router.get(
    "/initial-editing/submissions/{submission_id}/download",
    summary="Download Initial Editing PDF",
)
def download_initial_editing_pdf(
    submission_id: int,
    current_user: User = Depends(
        require_role("author", "editor", "eic")
    ),
    db: Session = Depends(get_db),
):
    # Step 1: Find the editing submission.
    submission = db.get(
        InitialEditingSubmission,
        submission_id,
    )

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Initial editing submission not found",
        )

    # Step 2: Find the related manuscript.
    manuscript = db.get(
        Manuscript,
        submission.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 3: Check permissions.
    is_author = (
        current_user.role == "author"
        and manuscript.author_id == current_user.user_id
    )

    is_editor = (
        current_user.role == "editor"
        and submission.editor_id == current_user.user_id
    )

    is_eic = current_user.role == "eic"

    if not (is_author or is_editor or is_eic):
        raise HTTPException(
            status_code=403,
            detail="You cannot access this editing submission",
        )

    # Step 4: Resolve the stored PDF path safely.
    upload_root = (
        BASE_DIR
        / "uploads"
        / "initial_editing"
        / str(manuscript.manuscript_id)
    ).resolve()

    pdf_path = (BASE_DIR / submission.file_path).resolve()

    if (
        not pdf_path.is_relative_to(upload_root)
        or pdf_path.suffix.lower() != ".pdf"
    ):
        raise HTTPException(
            status_code=500,
            detail="Invalid stored PDF path",
        )

    if not pdf_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Edited PDF file not found",
        )

    # Step 5: Return the PDF.
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=(
            f"manuscript_{manuscript.manuscript_id}"
            f"_initial_edit_{submission.submission_id}.pdf"
        ),
    )

@router.post(
    "/initial-editing/submissions/{submission_id}/author-review",
    summary="Review Initial Editing Submission as Author",
)
def review_initial_editing_as_author(
    submission_id: int,
    review_data: InitialEditingAuthorReview,
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the editing submission.
    submission = db.get(
        InitialEditingSubmission,
        submission_id,
    )

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Initial editing submission not found",
        )

    # Step 2: Find the related manuscript.
    manuscript = db.get(
        Manuscript,
        submission.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 3: Only the manuscript author can review.
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You are not the author of this manuscript",
        )

    # Step 4: Check workflow status.
    if manuscript.status != "initial_editing":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not in initial editing",
        )

    if submission.status != "pending_author":
        raise HTTPException(
            status_code=409,
            detail="This editing submission is not awaiting author review",
        )

    # Step 5: Record the author's decision.
    if review_data.decision == "approve":
        submission.status = "author_approved"
    else:
        submission.status = "author_revision_requested"

    submission.author_comments = review_data.comments
    submission.reviewed_at = datetime.now(timezone.utc)

    # Step 6: Notify the editor.
    db.add(
        Notification(
            user_id=submission.editor_id,
            manuscript_id=manuscript.manuscript_id,
            notification_type="initial_editing_author_review",
            message=(
                f"Author {current_user.name} reviewed initial editing "
                f"submission #{submission.submission_id}. "
                f"Decision: {review_data.decision}. "
                f"Comments: {review_data.comments}"
            ),
        )
    )

    # Step 7: Save changes.
    db.commit()
    db.refresh(submission)

    return {
        "submission_id": submission.submission_id,
        "manuscript_id": manuscript.manuscript_id,
        "author_id": current_user.user_id,
        "decision": review_data.decision,
        "status": submission.status,
        "author_comments": submission.author_comments,
        "reviewed_at": submission.reviewed_at,
        "message": "Author review submitted successfully",
    }

@router.post(
    "/initial-editing/submissions/{submission_id}/editor-approval",
    summary="Approve Initial Editing as Editor",
)
def approve_initial_editing_as_editor(
    submission_id: int,
    approval_data: InitialEditingEditorApproval,
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the editing submission.
    submission = db.get(
        InitialEditingSubmission,
        submission_id,
    )

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Initial editing submission not found",
        )

    # Step 2: Find the manuscript.
    manuscript = db.get(
        Manuscript,
        submission.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 3: Verify the assigned editor.
    invitation = db.scalar(
        select(InitialEditorInvitation).where(
            InitialEditorInvitation.manuscript_id
            == manuscript.manuscript_id,
            InitialEditorInvitation.editor_id
            == current_user.user_id,
            InitialEditorInvitation.status == "accepted",
        )
    )

    if (
        invitation is None
        or submission.editor_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=403,
            detail="You are not the assigned editor",
        )

    # Step 4: Check the workflow stage.
    if manuscript.status != "initial_editing":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not in initial editing",
        )

    # Alice must approve before Bob can make a decision.
    if submission.status != "author_approved":
        raise HTTPException(
            status_code=409,
            detail="Author approval is required first",
        )

    # Step 5: Record Bob's decision.
    if approval_data.decision == "approve":
        submission.status = "editor_approved"
    else:
        submission.status = "editor_revision_requested"

    submission.editor_comments = approval_data.comments
    submission.editor_reviewed_at = datetime.now(timezone.utc)

    # Step 6: Notify the appropriate person.
    if approval_data.decision == "approve":
        # Notify the EIC who invited Bob.
        recipient_id = invitation.invited_by
        message = (
            f"Editor {current_user.name} approved initial editing "
            f"submission #{submission_id} for manuscript "
            f"#{manuscript.manuscript_id}. "
            f"Comments: {approval_data.comments}"
        )
    else:
        # Notify the author that further changes are required.
        recipient_id = manuscript.author_id
        message = (
            f"Editor {current_user.name} requested further changes "
            f"for initial editing submission #{submission_id}. "
            f"Comments: {approval_data.comments}"
        )

    db.add(
        Notification(
            user_id=recipient_id,
            manuscript_id=manuscript.manuscript_id,
            notification_type="initial_editing_editor_approval",
            message=message,
        )
    )

    # Step 7: Commit both the decision and notification.
    db.commit()
    db.refresh(submission)

    return {
        "submission_id": submission.submission_id,
        "manuscript_id": manuscript.manuscript_id,
        "editor_id": current_user.user_id,
        "decision": approval_data.decision,
        "status": submission.status,
        "editor_comments": submission.editor_comments,
        "editor_reviewed_at": submission.editor_reviewed_at,
        "message": "Editor approval submitted successfully",
    }

@router.post(
    "/initial-editing/submissions/{submission_id}/eic-approval",
    summary="Approve Initial Editing as EIC",
)
def approve_initial_editing_as_eic(
    submission_id: int,
    approval_data: InitialEditingEICApproval,
    current_user: User = Depends(require_role("eic")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the initial editing submission.
    submission = db.get(
        InitialEditingSubmission,
        submission_id,
    )

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Initial editing submission not found",
        )

    # Step 2: Find the manuscript.
    manuscript = db.get(
        Manuscript,
        submission.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 3: Verify the workflow stage.
    if manuscript.status != "initial_editing":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not in initial editing",
        )

    # Bob must approve before Emma can review.
    if submission.status != "editor_approved":
        raise HTTPException(
            status_code=409,
            detail="Editor approval is required first",
        )

    # Step 4: Record Emma's decision.
    if approval_data.decision == "approve":
        submission.status = "eic_approved"
        submission.eic_approved_by = current_user.user_id

        # The author must still confirm the pre-review version.
        manuscript.status = "pre_review_confirmation"
    else:
        submission.status = "eic_revision_requested"
        submission.eic_approved_by = None

        # Return to initial editing.
        manuscript.status = "initial_editing"

    submission.eic_comments = approval_data.comments
    submission.eic_reviewed_at = datetime.now(timezone.utc)

    # Step 5: Notify Alice and Bob.
    recipients = {
        manuscript.author_id,
        submission.editor_id,
    }

    for recipient_id in recipients:
        db.add(
            Notification(
                user_id=recipient_id,
                manuscript_id=manuscript.manuscript_id,
                notification_type="initial_editing_eic_approval",
                message=(
                    f"EIC {current_user.name} reviewed initial editing "
                    f"submission #{submission_id}. "
                    f"Decision: {approval_data.decision}. "
                    f"Comments: {approval_data.comments}"
                ),
            )
        )

    # Step 6: Save the decision and notifications.
    db.commit()
    db.refresh(submission)
    db.refresh(manuscript)

    return {
        "submission_id": submission.submission_id,
        "manuscript_id": manuscript.manuscript_id,
        "eic_id": current_user.user_id,
        "decision": approval_data.decision,
        "submission_status": submission.status,
        "manuscript_status": manuscript.status,
        "eic_comments": submission.eic_comments,
        "eic_reviewed_at": submission.eic_reviewed_at,
        "eic_approved_by": submission.eic_approved_by,
        "message": "EIC approval submitted successfully",
    }

@router.post(
    "/initial-editing/submissions/{submission_id}/pre-review-confirmation",
    summary="Confirm Pre-review Version as Author",
)
def confirm_pre_review_version(
    submission_id: int,
    confirmation_data: PreReviewConfirmationRequest,
    current_user: User = Depends(require_role("author")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the approved editing submission.
    submission = db.get(InitialEditingSubmission, submission_id)

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Initial editing submission not found",
        )

    manuscript = db.get(Manuscript, submission.manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 2: Only the manuscript's author may confirm.
    if manuscript.author_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the manuscript author can confirm this version",
        )

    # Step 3: Verify the workflow state.
    if manuscript.status != "pre_review_confirmation":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not awaiting pre-review confirmation",
        )

    if submission.status != "eic_approved":
        raise HTTPException(
            status_code=409,
            detail="EIC approval is required first",
        )

    # Step 4: Prevent duplicate decisions for this version.
    existing = db.scalar(
        select(PreReviewConfirmation).where(
            PreReviewConfirmation.submission_id == submission_id
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This version has already been reviewed by the author",
        )

    # Step 5: Verify the exact EIC-approved PDF.
    upload_root = (BASE_DIR / "uploads" / "initial_editing").resolve()
    pdf_path = (BASE_DIR / submission.file_path).resolve()

    if (
        not pdf_path.is_relative_to(upload_root)
        or not pdf_path.is_file()
    ):
        raise HTTPException(
            status_code=409,
            detail="Approved PDF is missing or has an invalid path",
        )

    with pdf_path.open("rb") as pdf:
        if pdf.read(5) != b"%PDF-":
            raise HTTPException(
                status_code=409,
                detail="Approved file is not a valid PDF",
            )

    # Step 6: Record Alice's decision and the PDF version.
    confirmation = PreReviewConfirmation(
        manuscript_id=manuscript.manuscript_id,
        submission_id=submission.submission_id,
        author_id=current_user.user_id,
        file_path=submission.file_path,
        decision=confirmation_data.decision,
        comments=confirmation_data.comments,
    )

    db.add(confirmation)

    if confirmation_data.decision == "confirm":
        # The manuscript can now proceed to peer review.
        manuscript.status = "ready_for_peer_review"
    else:
        # Further editing is required.
        manuscript.status = "initial_editing"

    # Step 7: Notify the EIC and assigned editor.
    recipients = {submission.editor_id}

    if submission.eic_approved_by is not None:
        recipients.add(submission.eic_approved_by)

    for recipient_id in recipients:
        db.add(
            Notification(
                user_id=recipient_id,
                manuscript_id=manuscript.manuscript_id,
                notification_type="pre_review_confirmation",
                message=(
                    f"Author {current_user.name} reviewed pre-review "
                    f"version for manuscript #{manuscript.manuscript_id}. "
                    f"Decision: {confirmation_data.decision}. "
                    f"Comments: {confirmation_data.comments}"
                ),
            )
        )

    # Step 8: Save everything.
    db.commit()
    db.refresh(confirmation)
    db.refresh(manuscript)

    return {
        "confirmation_id": confirmation.confirmation_id,
        "manuscript_id": manuscript.manuscript_id,
        "submission_id": submission.submission_id,
        "author_id": confirmation.author_id,
        "decision": confirmation.decision,
        "comments": confirmation.comments,
        "file_path": confirmation.file_path,
        "confirmed_at": confirmation.confirmed_at,
        "manuscript_status": manuscript.status,
        "message": "Pre-review confirmation submitted successfully",
    }

@router.post(
    "/manuscripts/{manuscript_id}/decision",
    status_code=status.HTTP_201_CREATED,
    summary="Make EIC Decision After Peer Review",
)
def make_eic_decision(
    manuscript_id: int,
    decision_data: EICDecisionCreate,
    current_user: User = Depends(require_role("eic")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the manuscript.
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 2: Check the manuscript workflow status.
    if manuscript.status not in (
        "ready_for_peer_review",
        "revised",
    ):
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not ready for an EIC decision",
        )

    current_round = manuscript.review_round

    # Step 3: Get all reviewer assignments for this round.
    assignments = db.scalars(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.review_round == current_round,
        )
    ).all()

    if not assignments:
        raise HTTPException(
            status_code=409,
            detail="No reviewers assigned for this round",
        )

    # Step 4: Require every assigned reviewer to finish.
    for assignment in assignments:
        review = db.scalar(
            select(Review).where(
                Review.assignment_id == assignment.assignment_id,
            )
        )

        if review is None or assignment.status != "completed":
            raise HTTPException(
                status_code=409,
                detail="Not all reviewers have completed their reviews",
            )

    # Step 5: Prevent duplicate decisions for the same round.
    existing_decision = db.scalar(
        select(EICDecision).where(
            EICDecision.manuscript_id == manuscript_id,
            EICDecision.review_round == current_round,
        )
    )

    if existing_decision is not None:
        raise HTTPException(
            status_code=409,
            detail="An EIC decision already exists for this review round",
        )

    # Step 6: Map the decision to the next workflow status.
    status_map = {
        "accept": "final_editing",
        "reject": "rejected",
        "revision_required": "revision_required",
    }

    manuscript.status = status_map[decision_data.decision]

    # Step 7: Save the EIC decision history.
    decision = EICDecision(
        manuscript_id=manuscript_id,
        eic_id=current_user.user_id,
        review_round=current_round,
        decision=decision_data.decision,
        comments=decision_data.comments,
    )

    db.add(decision)

    # Step 8: Notify the author.
    db.add(
        Notification(
            user_id=manuscript.author_id,
            manuscript_id=manuscript_id,
            notification_type="eic_decision",
            message=(
                f"EIC decision for manuscript #{manuscript_id}, "
                f"round {current_round}: {decision_data.decision}. "
                f"Comments: {decision_data.comments}"
            ),
            is_read=False,
        )
    )

    # Step 9: Notify the editors responsible for this manuscript.
    editor_ids = {
        assignment.assigned_by
        for assignment in assignments
        if assignment.assigned_by is not None
    }

    for editor_id in editor_ids:
        if editor_id == current_user.user_id:
            continue

        db.add(
            Notification(
                user_id=editor_id,
                manuscript_id=manuscript_id,
                notification_type="eic_decision",
                message=(
                    f"EIC decision for manuscript #{manuscript_id}, "
                    f"round {current_round}: {decision_data.decision}. "
                    f"Comments: {decision_data.comments}"
                ),
                is_read=False,
            )
        )

    # Step 10: Save everything.
    db.commit()
    db.refresh(decision)
    db.refresh(manuscript)

    return {
        "decision_id": decision.decision_id,
        "manuscript_id": manuscript.manuscript_id,
        "eic_id": decision.eic_id,
        "review_round": decision.review_round,
        "decision": decision.decision,
        "comments": decision.comments,
        "manuscript_status": manuscript.status,
        "decided_at": decision.decided_at,
        "message": "EIC decision recorded successfully",
    }

@router.post(
    "/final-editing/{manuscript_id}/submit",
    status_code=status.HTTP_201_CREATED,
    summary="Submit Final Editing PDF",
)
def submit_final_editing(
    manuscript_id: int,
    file: UploadFile = File(...),
    comments: str = Form(...),
    current_user: User = Depends(require_role("editor")),
    db: Session = Depends(get_db),
):
    # Step 1: Find the manuscript.
    manuscript = db.get(Manuscript, manuscript_id)

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 2: Manuscript must be in final editing.
    if manuscript.status != "final_editing":
        raise HTTPException(
            status_code=409,
            detail="Manuscript is not in final editing",
        )

    # Step 3: Verify editor assignment.
    editor_assignment = db.scalar(
        select(ReviewAssignment).where(
            ReviewAssignment.manuscript_id == manuscript_id,
            ReviewAssignment.assigned_by == current_user.user_id,
        )
    )

    if editor_assignment is None:
        raise HTTPException(
            status_code=403,
            detail="You are not an assigned editor for this manuscript",
        )

    # Step 4: Prevent multiple pending submissions.
    pending_submission = db.scalar(
        select(FinalEditingSubmission).where(
            FinalEditingSubmission.manuscript_id == manuscript_id,
            FinalEditingSubmission.status.in_(
                ["pending_author", "author_approved"]
            ),
        )
    )

    if pending_submission is not None:
        raise HTTPException(
            status_code=409,
            detail="A final editing submission is awaiting approval",
        )

    # Step 5: Validate comments.
    comments = comments.strip()

    if not comments or len(comments) > 5000:
        raise HTTPException(
            status_code=400,
            detail="Comments must contain 1 to 5000 characters",
        )

    # Step 6: Validate PDF.
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed",
        )

    max_size = 10 * 1024 * 1024
    content = file.file.read(max_size + 1)

    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail="PDF file exceeds 10 MB limit",
        )

    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file",
        )

    # Step 7: Save final edited PDF.
    upload_dir = (
        BASE_DIR / "uploads" / "final_editing" / str(manuscript_id)
    )
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    filename = (
        f"editor_{current_user.user_id}_"
        f"{timestamp}_{uuid4().hex[:8]}.pdf"
    )
    file_path = upload_dir / filename

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=upload_dir,
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)

        os.replace(temp_path, file_path)

    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail="Could not save final edited PDF",
        )

    # Step 8: Create final editing record.
    submission = FinalEditingSubmission(
        manuscript_id=manuscript_id,
        editor_id=current_user.user_id,
        file_path=str(file_path.relative_to(BASE_DIR)),
        comments=comments,
        status="pending_author",
    )

    db.add(submission)

    # Step 9: Notify author.
    db.add(
        Notification(
            user_id=manuscript.author_id,
            manuscript_id=manuscript_id,
            notification_type="final_editing_submitted",
            message=(
                f"Editor {current_user.name} submitted a final edited "
                f"PDF for manuscript #{manuscript_id}. "
                f"Comments: {comments}"
            ),
        )
    )

    # Step 10: Commit.
    try:
        db.commit()
        db.refresh(submission)

    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    return {
        "submission_id": submission.submission_id,
        "manuscript_id": manuscript_id,
        "editor_id": current_user.user_id,
        "filename": filename,
        "file_path": submission.file_path,
        "comments": submission.comments,
        "status": submission.status,
        "created_at": submission.created_at,
        "message": "Final editing PDF submitted successfully",
    }

@router.get(
    "/final-editing/submissions/{submission_id}/download",
    summary="Download Final Editing PDF",
)
def download_final_editing_pdf(
    submission_id: int,
    current_user: User = Depends(
        require_role("author", "editor", "eic")
    ),
    db: Session = Depends(get_db),
):
    # Step 1: Find the final editing submission.
    submission = db.get(
        FinalEditingSubmission,
        submission_id,
    )

    if submission is None:
        raise HTTPException(
            status_code=404,
            detail="Final editing submission not found",
        )

    # Step 2: Find the related manuscript.
    manuscript = db.get(
        Manuscript,
        submission.manuscript_id,
    )

    if manuscript is None:
        raise HTTPException(
            status_code=404,
            detail="Manuscript not found",
        )

    # Step 3: Check permissions.
    is_author = (
        current_user.role == "author"
        and manuscript.author_id == current_user.user_id
    )

    is_editor = (
        current_user.role == "editor"
        and submission.editor_id == current_user.user_id
    )

    is_eic = current_user.role == "eic"

    if not (is_author or is_editor or is_eic):
        raise HTTPException(
            status_code=403,
            detail="You cannot access this final editing submission",
        )

    # Step 4: Resolve the PDF path safely.
    upload_root = (
        BASE_DIR
        / "uploads"
        / "final_editing"
        / str(manuscript.manuscript_id)
    ).resolve()

    pdf_path = (BASE_DIR / submission.file_path).resolve()

    if (
        not pdf_path.is_relative_to(upload_root)
        or pdf_path.suffix.lower() != ".pdf"
    ):
        raise HTTPException(
            status_code=500,
            detail="Invalid stored PDF path",
        )

    if not pdf_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Final edited PDF file not found",
        )

    # Step 5: Return the PDF.
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=(
            f"manuscript_{manuscript.manuscript_id}"
            f"_final_edit_{submission.submission_id}.pdf"
        ),
    )
