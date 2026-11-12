import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault("SECRET_KEY", "isolated-test-secret")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app import models
import app.main as main
import app.review_files as review_files
import app.routers.manuscripts as manuscripts
from app.routers.users import get_current_user

spec = importlib.util.spec_from_file_location("review_migration", Path(__file__).resolve().parents[1] / "migrations/001_reviewer_file_bindings.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
invitation_spec = importlib.util.spec_from_file_location("invitation_migration", Path(__file__).resolve().parents[1] / "migrations/002_reviewer_invitations.py")
invitation_migration = importlib.util.module_from_spec(invitation_spec)
invitation_spec.loader.exec_module(invitation_migration)
policy_spec = importlib.util.spec_from_file_location("policy_migration", Path(__file__).resolve().parents[1] / "migrations/003_two_reviewer_policy.py")
policy_migration = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy_migration)
agreement_spec = importlib.util.spec_from_file_location("agreement_migration", Path(__file__).resolve().parents[1] / "migrations/004_author_agreements.py")
agreement_migration = importlib.util.module_from_spec(agreement_spec)
agreement_spec.loader.exec_module(agreement_migration)
final_spec = importlib.util.spec_from_file_location("final_migration", Path(__file__).resolve().parents[1] / "migrations/005_final_editor_workflow.py")
final_migration = importlib.util.module_from_spec(final_spec)
final_spec.loader.exec_module(final_migration)

formatter_spec = importlib.util.spec_from_file_location("formatter_migration", Path(__file__).resolve().parents[1] / "migrations/006_formatter_publication.py")
formatter_migration = importlib.util.module_from_spec(formatter_spec)
formatter_spec.loader.exec_module(formatter_migration)

class ReviewerDownloadsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite"
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        old_tables = [t for t in Base.metadata.sorted_tables if t.name not in {"review_round_files", "review_assignment_files", "reviewer_invitations", "manuscript_review_policies", "author_agreements", "final_editor_invitations", "final_editing_submission_invitations", "formatter_invitations", "formatting_submissions", "prototype_publications"}]
        Base.metadata.create_all(self.engine, tables=old_tables)
        migration.migrate(self.db_path)
        migration.migrate(self.db_path)
        invitation_migration.migrate(self.db_path)
        invitation_migration.migrate(self.db_path)
        policy_migration.migrate(self.db_path)
        policy_migration.migrate(self.db_path)
        agreement_migration.migrate(self.db_path)
        agreement_migration.migrate(self.db_path)
        final_migration.migrate(self.db_path)
        final_migration.migrate(self.db_path)
        formatter_migration.migrate(self.db_path)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.author = models.User(name="Author", email="author@example.com", password_hash="x", role="author")
        self.editor = models.User(name="Editor", email="editor@example.com", password_hash="x", role="editor")
        self.reviewer = models.User(name="Reviewer", email="reviewer@example.com", password_hash="x", role="reviewer")
        self.other = models.User(name="Other", email="other@example.com", password_hash="x", role="reviewer")
        self.db.add_all([self.author, self.editor, self.reviewer, self.other])
        self.db.flush()
        self.manuscript = models.Manuscript(title="Title", abstract="Abstract", author_id=self.author.user_id, status="ready_for_peer_review", review_round=1)
        self.db.add(self.manuscript)
        self.db.flush()
        self.mid = self.manuscript.manuscript_id
        self.pdf = self.write(f"uploads/initial_editing/{self.mid}/edited.pdf", b"%PDF-first")
        submission = models.InitialEditingSubmission(manuscript_id=self.mid, editor_id=self.editor.user_id, file_path=self.pdf, comments="ok", status="eic_approved")
        self.db.add(submission)
        self.db.flush()
        self.db.add(models.PreReviewConfirmation(manuscript_id=self.mid, submission_id=submission.submission_id, author_id=self.author.user_id, file_path=self.pdf, decision="confirm", comments="ok"))
        self.db.add(models.InitialEditorInvitation(manuscript_id=self.mid, editor_id=self.editor.user_id, invited_by=self.editor.user_id, status="accepted"))
        self.db.commit()
        self.current = self.editor
        main.app.dependency_overrides[get_db] = lambda: self.db
        main.app.dependency_overrides[get_current_user] = lambda: self.current
        self.patches = [patch.object(main, "engine", self.engine), patch.object(review_files, "BASE_DIR", self.root), patch.object(manuscripts, "BASE_DIR", self.root)]
        for item in self.patches: item.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        for item in reversed(self.patches): item.stop()
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative

    def assign(self, reviewer, round_number):
        self.current = self.editor
        response = self.client.post("/assignments/", json={"manuscript_id": self.mid, "reviewer_id": reviewer.user_id, "review_round": round_number})
        if response.status_code == 201:
            self.current = reviewer
            accepted = self.client.post(f"/assignments/{response.json()['assignment_id']}/invitation-response", json={"decision": "accept"})
            assert accepted.status_code == 200, accepted.text
        return response

    def test_first_round_binding_and_access(self):
        response = self.assign(self.reviewer, 1)
        self.assertEqual(response.status_code, 201, response.text)
        assignment_id = response.json()["assignment_id"]
        second = self.assign(self.other, 1)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(self.db.query(models.ReviewRoundFile).count(), 1)
        self.current = self.reviewer
        response = self.client.get(f"/assignments/{assignment_id}/download")
        self.assertEqual(response.content, b"%PDF-first")
        self.assertIn(".pdf", response.headers["content-disposition"])
        self.assertEqual(self.client.get(f"/manuscripts/{self.mid}/download").status_code, 403)
        self.assertEqual(self.client.get(f"/manuscripts/{self.mid}/versions").status_code, 403)
        self.assertEqual(self.client.get(f"/manuscripts/{self.mid}/versions/other.pdf").status_code, 403)
        self.current = self.other
        self.assertEqual(self.client.get(f"/assignments/{assignment_id}/download").status_code, 403)
        self.current = self.author
        self.assertNotEqual(self.client.get(f"/manuscripts/{self.mid}/versions").status_code, 403)

    def test_later_round_formats_and_legacy(self):
        legacy = models.ReviewAssignment(manuscript_id=self.mid, reviewer_id=self.reviewer.user_id, assigned_by=self.editor.user_id, review_round=1, status="completed")
        self.db.add(legacy)
        self.db.commit()
        self.current = self.reviewer
        self.assertEqual(self.client.get(f"/assignments/{legacy.assignment_id}/download").status_code, 409)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as docx:
            docx.writestr("[Content_Types].xml", "x")
            docx.writestr("word/document.xml", "x")
        for round_number, extension, content in [(2, "pdf", b"%PDF-revised"), (3, "docx", archive.getvalue()), (4, "doc", bytes.fromhex("D0CF11E0A1B11AE1") + b"document")]:
            self.manuscript.status = "revision_required"
            self.manuscript.review_round = round_number - 1
            self.manuscript.file_path = self.write(f"uploads/manuscripts/{self.mid}/round_{round_number}_test.{extension}", content)
            self.db.commit()
            self.current = self.author
            response = self.client.post(f"/manuscripts/{self.mid}/resubmit", json={"title": "Updated"})
            self.assertEqual(response.status_code, 200, response.text)
            response = self.assign(self.reviewer, round_number)
            self.assertEqual(response.status_code, 201, response.text)
            assignment_id = response.json()["assignment_id"]
            self.current = self.reviewer
            response = self.client.get(f"/assignments/{assignment_id}/download")
            self.assertEqual(response.content, content)
            self.assertIn(f".{extension}", response.headers["content-disposition"])
            self.manuscript.file_path = None
            self.db.commit()
            self.assertEqual(self.client.get(f"/assignments/{assignment_id}/download").content, content)
        self.assertEqual(self.db.scalar(select(models.ReviewAssignment).where(models.ReviewAssignment.assignment_id == legacy.assignment_id)).status, "completed")

    def test_missing_file_and_invalid_binding_fail_closed(self):
        response = self.assign(self.reviewer, 1)
        self.assertEqual(response.status_code, 201, response.text)
        assignment_id = response.json()["assignment_id"]
        assignment = self.db.get(models.ReviewAssignment, assignment_id)
        assignment.status = "completed"
        self.db.commit()
        self.current = self.reviewer
        self.assertEqual(self.client.get(f"/assignments/{assignment_id}/download").status_code, 200)
        (self.root / self.pdf).unlink()
        self.assertEqual(self.client.get(f"/assignments/{assignment_id}/download").status_code, 409)
        self.assertFalse(self.client.get("/assignments/my").json()[0]["review_file_available"])
        self.write(self.pdf, b"%PDF-first")
        assignment.review_round = 2
        self.db.commit()
        self.assertEqual(self.client.get(f"/assignments/{assignment_id}/download").status_code, 409)

    def test_unrecorded_legacy_round_cannot_be_assigned(self):
        self.manuscript.status = "revised"
        self.manuscript.review_round = 2
        self.db.commit()
        self.assertEqual(self.assign(self.reviewer, 2).status_code, 409)

    def test_migration_refuses_partial_schema(self):
        import sqlite3
        path = self.root / "partial.sqlite"
        other = create_engine(f"sqlite:///{path}")
        old_tables = [t for t in Base.metadata.sorted_tables if t.name not in {"review_round_files", "review_assignment_files"}]
        Base.metadata.create_all(other, tables=old_tables)
        other.dispose()
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE review_round_files (broken INTEGER)")
        with self.assertRaises(RuntimeError):
            migration.migrate(path)
        with sqlite3.connect(path) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())

    def test_startup_refuses_unmigrated_database_without_creating_tables(self):
        import sqlite3
        path = self.root / "unmigrated.sqlite"
        other = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        old_tables = [t for t in Base.metadata.sorted_tables if t.name not in {"review_round_files", "review_assignment_files"}]
        Base.metadata.create_all(other, tables=old_tables)
        with patch.object(main, "engine", other):
            with self.assertRaisesRegex(RuntimeError, "migration"):
                with TestClient(main.app):
                    pass
        other.dispose()
        with sqlite3.connect(path) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='review_round_files'").fetchone())


if __name__ == "__main__":
    unittest.main()
