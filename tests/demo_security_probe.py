"""Subprocess probe; only run with an explicitly initialized temporary demo."""
import json
from pathlib import Path
import os
import tempfile

root = Path(os.environ["MANUSCRIPT_DEMO_ROOT"]).resolve(strict=True)
if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
    raise RuntimeError("Tests require a temporary demo root")

from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import User, Manuscript, InitialEditorInvitation, ReviewAssignment
from app.security import hash_password
from app.demo_gateway import app

accounts = json.loads((root / "accounts.json").read_text())
with SessionLocal() as db:
    editor = db.query(User).filter_by(email=accounts["editor"]["email"]).one()
    author = db.query(User).filter_by(email=accounts["author"]["email"]).one()
    manuscript = db.query(Manuscript).one()
    manuscript.status = "revised"
    db.add(InitialEditorInvitation(manuscript_id=manuscript.manuscript_id, editor_id=editor.user_id, invited_by=editor.user_id, status="accepted"))
    reviewer = db.query(User).filter_by(email=accounts["reviewer"]["email"]).one()
    db.add(ReviewAssignment(manuscript_id=manuscript.manuscript_id, reviewer_id=reviewer.user_id, assigned_by=editor.user_id, review_round=1, status="assigned"))
    db.add(User(name="Unassigned Editor", email="unassigned@example.com", password_hash=hash_password("temporary-password"), role="editor"))
    db.commit()
    mid = manuscript.manuscript_id

with TestClient(app, raise_server_exceptions=False) as client:
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    for path in ("/docs", "/redoc", "/openapi.json", "/api/docs", "/api/redoc", "/api/openapi.json", "/.demo_data/accounts.json", "/api/not-a-route"):
        assert client.get(path).status_code == 404, path
    assert client.post("/api/users/register", json={"name":"X", "email":"x@example.com", "password":"strongpassword"}).status_code == 403
    def login(email, password):
        return client.post("/api/users/login", json={"email":email, "password":password})
    author_token = login(accounts["author"]["email"], accounts["author"]["password"]).json()["access_token"]
    editor_token = login(accounts["editor"]["email"], accounts["editor"]["password"]).json()["access_token"]
    other_token = login("unassigned@example.com", "temporary-password").json()["access_token"]
    def auth(token): return {"Authorization": "Bearer " + token}
    assert client.get(f"/api/assignments/manuscript/{mid}", headers=auth(editor_token)).status_code == 200
    for path in (f"/api/assignments/manuscript/{mid}", f"/api/assignments/manuscript/{mid}/summary"):
        assert client.get(path, headers=auth(other_token)).status_code == 403
    assert client.patch(f"/api/manuscripts/{mid}/decision", headers=auth(other_token), json={"decision":"accepted"}).status_code == 403
    assert client.get("/api/assignments/", headers=auth(other_token)).json() == []
    assert len(client.get("/api/assignments/", headers=auth(editor_token)).json()) == 1
    assert client.get(f"/api/manuscripts/{mid}/download", headers=auth(author_token)).status_code == 200
    with SessionLocal() as db:
        manuscript = db.get(Manuscript, mid)
        manuscript.file_path = str(Path(__file__).resolve().parents[1] / "uploads" / "secret.pdf")
        db.commit()
    assert client.get(f"/api/manuscripts/{mid}/download", headers=auth(author_token)).status_code == 409
    target = root / "uploads" / "manuscripts" / str(mid) / "escape.pdf"
    target.symlink_to(Path(__file__).resolve().parents[1] / "README.md")
    with SessionLocal() as db:
        manuscript = db.get(Manuscript, mid)
        manuscript.file_path = str(target.relative_to(root))
        db.commit()
    assert client.get(f"/api/manuscripts/{mid}/download", headers=auth(author_token)).status_code >= 400
    # At least ten attempts from this client in the window are limited, even with forged forwarding headers.
    results = [client.post("/api/users/login", headers={"X-Forwarded-For": str(i)}, json={"email":"wrong@example.com", "password":"incorrect"}).status_code for i in range(11)]
    assert 429 in results and results[-1] == 429, results

# Child backend lifespan must reject an unmigrated DB through the gateway.
from sqlalchemy import text
with SessionLocal() as db:
    db.execute(text("DELETE FROM schema_migrations"))
    db.commit()
try:
    with TestClient(app):
        raise AssertionError("Missing migration was accepted")
except RuntimeError as error:
    assert "migration" in str(error).lower()
print("PASS isolated gateway, docs/registration, author/editor authorization, path escape, and rate limit")
