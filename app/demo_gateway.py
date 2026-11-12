"""Same-origin gateway for an explicitly configured, isolated demo only."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import IS_DEMO, PROJECT_ROOT, DATA_ROOT
from app.main import app as backend
import json
import stat

if not IS_DEMO:
    raise RuntimeError("The demo gateway requires demo mode")
credentials = DATA_ROOT / "accounts.json"
uploads = DATA_ROOT / "uploads"
if (not (DATA_ROOT / "manuscript.db").is_file() or not uploads.is_dir() or uploads.is_symlink()
        or credentials.is_symlink() or not credentials.is_file()
        or credentials.stat().st_nlink != 1 or stat.S_IMODE(credentials.stat().st_mode) & 0o077):
    raise RuntimeError("Demo database, uploads, or private account credentials are missing or unsafe")
try:
    accounts = json.loads(credentials.read_text())
    roles = ("author", "editor", "reviewer", "eic")
    if not all(isinstance(accounts.get(role), dict) and accounts[role].get("email")
               and isinstance(accounts[role].get("password"), str) and len(accounts[role]["password"]) >= 20 for role in roles):
        raise ValueError("Missing demo accounts")
    if len({accounts[role]["password"] for role in roles}) != len(roles):
        raise ValueError("Demo account passwords must be unique")
except (ValueError, TypeError, AttributeError):
    raise RuntimeError("Demo account credentials are invalid") from None
dist = PROJECT_ROOT / "frontend" / "dist"
if not (dist / "index.html").is_file() or dist.is_symlink():
    raise RuntimeError("Build the frontend before starting the demo")

@asynccontextmanager
async def lifespan(_):
    async with backend.router.lifespan_context(backend):
        yield

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/api", backend)
app.mount("/", StaticFiles(directory=dist, html=True, follow_symlink=False), name="frontend")
