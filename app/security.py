
import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash
from app.config import DATA_ROOT, IS_DEMO, PROJECT_ROOT
import stat
from dotenv import dotenv_values


# Load environment variables from .env
if IS_DEMO:
    key_path = DATA_ROOT / "jwt.key"
    if key_path.is_symlink() or not key_path.is_file() or key_path.stat().st_nlink != 1 or stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        raise RuntimeError("Demo JWT key missing or unsafe")
    SECRET_KEY = key_path.read_text().strip()
    if len(SECRET_KEY) < 64 or len(set(SECRET_KEY)) < 12 or any(c not in "0123456789abcdef" for c in SECRET_KEY):
        raise RuntimeError("Demo JWT key must be a randomly generated 256-bit hex key")
    development_keys = (os.environ.get("SECRET_KEY"), dotenv_values(PROJECT_ROOT / ".env").get("SECRET_KEY"))
    if any(key and SECRET_KEY == key for key in development_keys):
        raise RuntimeError("Demo JWT key must differ from the development key")
else:
    load_dotenv()
    SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Password hashing
password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


# Create a JWT access token
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(user_id),
        "exp": expire
    }

    token = jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return token


# Verify and decode a JWT access token
def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]}
        )

        user_id = int(payload["sub"])

        if user_id <= 0:
            return None

        return user_id

    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
