"""Development defaults and fail-closed local demo configuration."""
import os
from pathlib import Path
import stat

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODE = os.environ.get("MANUSCRIPT_MODE", "development")
if MODE not in {"development", "demo"}:
    raise RuntimeError("Invalid MANUSCRIPT_MODE")
IS_DEMO = MODE == "demo"


def demo_root(value: str) -> Path:
    if not value or not Path(value).is_absolute():
        raise RuntimeError("Demo root must be an explicit absolute path")
    path = Path(value)
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise RuntimeError("Demo root must be an existing non-symlink canonical directory")
    root = path.resolve()
    development_uploads = (PROJECT_ROOT / "uploads").resolve()
    if (root == PROJECT_ROOT or PROJECT_ROOT.is_relative_to(root)
            or root.is_relative_to(development_uploads)
            or (root.is_relative_to(PROJECT_ROOT) and root != PROJECT_ROOT / ".demo_data")):
        raise RuntimeError("Demo root overlaps development data")
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise RuntimeError("Demo root must be private (mode 0700)")
    for item in (root / "manuscript.db", root / "uploads", root / "jwt.key"):
        if item.is_symlink() or (item.exists() and item.resolve().parent != root):
            raise RuntimeError("Unsafe demo data path")
    db = root / "manuscript.db"
    if db.exists() and (not db.is_file() or db.stat().st_nlink != 1 or stat.S_IMODE(db.stat().st_mode) & 0o077):
        raise RuntimeError("Unsafe demo database")
    return root


DATA_ROOT = demo_root(os.environ.get("MANUSCRIPT_DEMO_ROOT", "")) if IS_DEMO else PROJECT_ROOT


def guard_demo_path(path: Path) -> Path:
    """Reject paths and symlinks outside demo uploads, including missing targets."""
    path = Path(path)
    if not IS_DEMO:
        return path
    uploads = DATA_ROOT / "uploads"
    if uploads.is_symlink() or not path.is_absolute():
        raise RuntimeError("Unsafe demo upload path")
    resolved = path.resolve()
    if not resolved.is_relative_to(uploads) or not path.is_relative_to(uploads):
        raise RuntimeError("Demo file outside upload root")
    current = uploads
    for component in path.relative_to(uploads).parts:
        current = current / component
        if current.is_symlink():
            raise RuntimeError("Symlinks are not permitted in demo uploads")
        if current.is_file() and current.stat().st_nlink != 1:
            raise RuntimeError("Hard-linked demo files are not permitted")
    return path
