"""Secure token helpers for email verification."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

TOKEN_TTL_HOURS = 24


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def generate_verification_token() -> tuple[str, str]:
    """
    Generate a random token and its SHA-256 digest.

    Returns:
        (raw_token, token_digest)
    """
    raw_token = secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    return raw_token, token_digest


def digest_verification_token(raw_token: str) -> str:
    """Compute the SHA-256 digest of a verification token."""
    return hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()


def token_expiration() -> datetime:
    """Return the expiration time, 24 hours from now."""
    return utc_now() + timedelta(hours=TOKEN_TTL_HOURS)


def to_db_timestamp(value: datetime) -> str:
    """
    Convert an aware datetime to a UTC timestamp
    suitable for SQLite TEXT columns.
    """
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
