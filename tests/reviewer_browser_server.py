"""Disposable browser-test server. Never use with production paths or data."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.middleware.cors import CORSMiddleware
import uvicorn

root = Path(os.environ["REVIEWER_E2E_ROOT"]).resolve(strict=True)
if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.name.startswith("reviewer-e2e-"):
    raise RuntimeError("Refusing non-temporary test root")
os.environ["SECRET_KEY"] = "temporary-browser-test-secret"

from app.database import Base, get_db
from app import models
from app.security import hash_password, create_access_token
import app.main as main
import app.review_files as review_files
import app.routers.manuscripts as manuscripts
import app.routers.assignments as assignments
import app.routers.eic as eic

db_path = root / "browser.sqlite"
if db_path.exists():
    raise RuntimeError("Refusing to use an existing test database")
engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
@event.listens_for(engine, "connect")
def foreign_keys(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")

base_tables = [t for t in Base.metadata.sorted_tables if t.name not in {"review_round_files", "review_assignment_files", "reviewer_invitations", "manuscript_review_policies", "author_agreements", "final_editor_invitations", "final_editing_submission_invitations", "formatter_invitations", "formatting_submissions", "prototype_publications"}]
Base.metadata.create_all(engine, tables=base_tables)
spec = importlib.util.spec_from_file_location("migration", Path(__file__).resolve().parents[1] / "migrations/001_reviewer_file_bindings.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
spec = importlib.util.spec_from_file_location("invitation_migration", Path(__file__).resolve().parents[1] / "migrations/002_reviewer_invitations.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
spec = importlib.util.spec_from_file_location("policy_migration", Path(__file__).resolve().parents[1] / "migrations/003_two_reviewer_policy.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
spec = importlib.util.spec_from_file_location("agreement_migration", Path(__file__).resolve().parents[1] / "migrations/004_author_agreements.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
spec = importlib.util.spec_from_file_location("final_migration", Path(__file__).resolve().parents[1] / "migrations/005_final_editor_workflow.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
spec = importlib.util.spec_from_file_location("formatter_migration", Path(__file__).resolve().parents[1] / "migrations/006_formatter_publication.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(db_path)
Session = sessionmaker(bind=engine, expire_on_commit=False)

def write(path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return path

archive = io.BytesIO()
with zipfile.ZipFile(archive, "w") as file:
    file.writestr("[Content_Types].xml", "fixture")
    file.writestr("word/document.xml", "fixture")
payloads = {"pdf": b"%PDF-round-two-fixture", "docx": archive.getvalue(), "doc": bytes.fromhex("D0CF11E0A1B11AE1") + b"round-two-fixture"}
password = "temporary-test-password"
with Session() as db:
    users = {}
    for role in ("author", "editor", "eic", "reviewer", "other", "third"):
        user = models.User(name=role.title(), email=f"{role}@example.com", password_hash=hash_password(password), role="reviewer" if role in ("other", "third") else role)
        db.add(user)
        users[role] = user
    db.flush()
    fixture = {"password": password, "tokens": {role: create_access_token(user.user_id) for role, user in users.items()}, "user_ids": {role: user.user_id for role, user in users.items()}, "assignments": {}}
    for label in ("round1", "pdf", "docx", "doc", "legacy"):
        round_no = 1 if label in ("round1", "legacy") else 2
        manuscript = models.Manuscript(title=f"Fixture {label}", abstract="Test fixture", author_id=users["author"].user_id, status="ready_for_peer_review" if round_no == 1 else "revised", review_round=round_no)
        db.add(manuscript)
        db.flush()
        mid = manuscript.manuscript_id
        manuscript.file_path = write(f"uploads/manuscripts/{mid}/original.pdf", b"%PDF-original-fixture")
        db.add(models.InitialEditorInvitation(manuscript_id=mid, editor_id=users["editor"].user_id, invited_by=users["eic"].user_id, status="accepted"))
        if label == "round1":
            path = write(f"uploads/initial_editing/{mid}/confirmed.pdf", b"%PDF-confirmed-initial-edit")
            edit = models.InitialEditingSubmission(manuscript_id=mid, editor_id=users["editor"].user_id, file_path=path, comments="fixture", status="eic_approved")
            db.add(edit)
            db.flush()
            confirmation = models.PreReviewConfirmation(manuscript_id=mid, submission_id=edit.submission_id, author_id=users["author"].user_id, file_path=path, decision="confirm", comments="fixture")
            db.add(confirmation)
            db.flush()
            round_file = models.ReviewRoundFile(manuscript_id=mid, review_round=1, source_kind="initial_editing", confirmation_id=confirmation.confirmation_id, file_path=path)
        elif label != "legacy":
            path = write(f"uploads/manuscripts/{mid}/revised.{label}", payloads[label])
            round_file = models.ReviewRoundFile(manuscript_id=mid, review_round=2, source_kind="author_revision", file_path=path)
        assignment = models.ReviewAssignment(manuscript_id=mid, reviewer_id=users["reviewer"].user_id, assigned_by=users["editor"].user_id, review_round=round_no, status="completed" if label == "legacy" else "assigned")
        db.add(assignment)
        db.flush()
        if label != "legacy":
            db.add(round_file)
            db.flush()
            db.add(models.ReviewAssignmentFile(assignment_id=assignment.assignment_id, round_file_id=round_file.round_file_id))
        fixture["assignments"][label] = {"id": assignment.assignment_id, "manuscript_id": mid, "round": round_no}
    db.commit()
    (root / "fixtures.json").write_text(json.dumps(fixture))

main.engine = engine
for module in (review_files, manuscripts, assignments, eic):
    module.BASE_DIR = root
def isolated_db():
    with Session() as db:
        yield db
main.app.dependency_overrides[get_db] = isolated_db
main.app.add_middleware(CORSMiddleware, allow_origins=[os.environ["REVIEWER_E2E_ORIGIN"]], allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["REVIEWER_E2E_BACKEND_PORT"]), log_level="warning")
