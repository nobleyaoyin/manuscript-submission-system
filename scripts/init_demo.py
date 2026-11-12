"""Create a new synthetic demo only; never reset an existing directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data-root", required=True)
args = parser.parse_args()
root = Path(args.data_root)
if not root.is_absolute() or root.exists() or root.is_symlink() or root.resolve() != root:
    raise SystemExit("Demo root must be a new, absolute, canonical path")
if (root == project or project.is_relative_to(root) or root.is_relative_to((project / "uploads").resolve())
        or (root.is_relative_to(project) and root != project / ".demo_data")):
    raise SystemExit("Demo root overlaps development data")
os.umask(0o077)
root.mkdir(mode=0o700, parents=False, exist_ok=False)
(root / "uploads").mkdir(mode=0o700)
(root / "jwt.key").write_text(secrets.token_hex(32))
os.environ["MANUSCRIPT_MODE"] = "demo"
os.environ["MANUSCRIPT_DEMO_ROOT"] = str(root)

from app.database import Base, engine, SessionLocal
from app import models
from app.security import hash_password

# Tables owned by explicit migrations must never be created by metadata.
# The initializer applies only 001–006; 007–009 remain separate operations.
migration_owned_tables = {
    # 001–006
    "review_round_files", "review_assignment_files", "reviewer_invitations",
    "manuscript_review_policies", "author_agreements", "final_editor_invitations",
    "final_editing_submission_invitations", "formatter_invitations",
    "formatting_submissions", "prototype_publications",
    # 007–009
    "notification_events", "email_outbox", "email_delivery_attempts",
    "user_email_verifications", "email_verification_challenges",
    "account_lifecycle_events", "account_lifecycle", "reviewer_invitation_invalidations",
}
base_tables = [table for table in Base.metadata.sorted_tables if table.name not in migration_owned_tables]
Base.metadata.create_all(engine, tables=base_tables)
spec = importlib.util.spec_from_file_location("demo_migration", project / "migrations/001_reviewer_file_bindings.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
spec = importlib.util.spec_from_file_location("demo_invitation_migration", project / "migrations/002_reviewer_invitations.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
spec = importlib.util.spec_from_file_location("demo_policy_migration", project / "migrations/003_two_reviewer_policy.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
spec = importlib.util.spec_from_file_location("demo_agreement_migration", project / "migrations/004_author_agreements.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
spec = importlib.util.spec_from_file_location("demo_final_migration", project / "migrations/005_final_editor_workflow.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
spec = importlib.util.spec_from_file_location("demo_formatter_migration", project / "migrations/006_formatter_publication.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
migration.migrate(root / "manuscript.db")
credentials = {}
with SessionLocal() as db:
    for role in ("author", "editor", "reviewer", "eic", "formatter"):
        password = secrets.token_urlsafe(24)
        email = f"{role}@demo.example.com"
        db.add(models.User(name=f"Demo {role.title()}", email=email, password_hash=hash_password(password), role=role))
        credentials[role] = {"email": email, "password": password}
    db.flush()
    author = db.query(models.User).filter_by(email=credentials["author"]["email"]).one()
    manuscript = models.Manuscript(title="Synthetic Demo Manuscript", abstract="A fictional manuscript for demonstration.", author_id=author.user_id, status="draft", review_round=1)
    db.add(manuscript)
    db.flush()
    folder = root / "uploads" / "manuscripts" / str(manuscript.manuscript_id)
    folder.mkdir(parents=True, mode=0o700)
    file = folder / "demo.pdf"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length 57 >>\nstream\nBT /F1 14 Tf 30 100 Td (Synthetic demo manuscript) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    file.write_bytes(pdf)
    manuscript.file_path = str(file.relative_to(root))
    db.commit()
(root / "accounts.json").write_text(json.dumps(credentials, indent=2))
print(f"Demo initialized at {root}. Private account credentials are in {root / 'accounts.json'}")
print("No development data was imported. A synthetic draft was created.")
