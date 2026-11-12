"""Email verification persistence and single-use token consumption.

The caller owns commit/rollback. No email is sent here.
"""

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    User,
    UserEmailVerification,
    EmailVerificationChallenge,
)
from app.email.verification_tokens import (
    generate_verification_token,
    digest_verification_token,
    utc_now,
    to_db_timestamp,
    TOKEN_TTL_HOURS,
)
from datetime import timedelta


class EmailVerificationError(ValueError):
    pass


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _lock_sqlite_writer(db: Session) -> None:
    """Acquire SQLite's writer lock before reading verification state."""
    connection = db.connection()

    if connection.dialect.name != "sqlite":
        raise EmailVerificationError("SQLite is required")

    driver = connection.connection.driver_connection

    if not driver.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        # A transaction already exists. The caller must ensure that
        # it has not started as a stale SQLite read snapshot.
        connection.exec_driver_sql(
            "UPDATE users SET user_id = user_id WHERE 0"
        )


def create_verification_challenge(db: Session, user: User) -> str:
    """Issue a new token and revoke older unused tokens. Never commit."""
    if user.user_id is None:
        raise EmailVerificationError("User must be persisted first")

    _lock_sqlite_writer(db)

    db.refresh(user)
    email = _normalize_email(user.email)
    now_dt = utc_now()
    now = to_db_timestamp(now_dt)

    state = db.get(UserEmailVerification, user.user_id)

    if state is None:
        state = UserEmailVerification(
            user_id=user.user_id,
            email=email,
            verified_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
    elif state.email != email:
        state.email = email
        state.verified_at = None
        state.updated_at = now

    # Invalidate all earlier unused challenges for this user.
    db.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.user_id == user.user_id,
            EmailVerificationChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    raw_token, digest = generate_verification_token()

    db.add(
        EmailVerificationChallenge(
            challenge_id=str(uuid4()),
            user_id=user.user_id,
            email=email,
            token_digest=digest,
            created_at=now,
            expires_at=to_db_timestamp(
                now_dt + timedelta(hours=TOKEN_TTL_HOURS)
            ),
            consumed_at=None,
        )
    )

    db.flush()
    return raw_token


def consume_verification_token(db: Session, raw_token: str) -> bool:
    """Consume a current, unexpired token once. Caller must commit."""
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 512:
        return False

    _lock_sqlite_writer(db)

    digest = digest_verification_token(raw_token)
    now = to_db_timestamp(utc_now())

    challenge = db.scalar(
        select(EmailVerificationChallenge).where(
            EmailVerificationChallenge.token_digest == digest
        )
    )

    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
    ):
        return False

    user = db.get(User, challenge.user_id, populate_existing=True)

    if user is None or _normalize_email(user.email) != challenge.email:
        return False

    result = db.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.challenge_id == challenge.challenge_id,
            EmailVerificationChallenge.consumed_at.is_(None),
            EmailVerificationChallenge.expires_at > now,
        )
        .values(consumed_at=now)
    )

    if result.rowcount != 1:
        return False

    state = db.get(UserEmailVerification, user.user_id)

    if state is None:
        state = UserEmailVerification(
            user_id=user.user_id,
            email=challenge.email,
            verified_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
    else:
        state.email = challenge.email
        state.verified_at = now
        state.updated_at = now

    # Revoke any other outstanding challenges.
    db.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.user_id == user.user_id,
            EmailVerificationChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    db.flush()
    return True


def is_email_verified(db: Session, user: User) -> bool:
    """Return True only for the user's current verified email."""
    state = db.get(UserEmailVerification, user.user_id)

    return bool(
        state
        and state.verified_at is not None
        and state.email == _normalize_email(user.email)
    )


class VerificationCooldownError(EmailVerificationError):
    """A verification challenge was issued too recently."""


def create_resend_challenge(
    db: Session,
    user: User,
    cooldown_seconds: int = 60,
) -> str:
    """Issue a replacement token only after the cooldown.

    The caller owns commit/rollback.
    """
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be nonnegative")

    if user.user_id is None:
        raise EmailVerificationError("User must be persisted first")

    _lock_sqlite_writer(db)

    now_dt = utc_now()

    latest = db.scalar(
        select(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.user_id == user.user_id
        )
        .order_by(
            EmailVerificationChallenge.created_at.desc()
        )
        .limit(1)
    )

    if latest is not None:
        from datetime import datetime

        created_at = datetime.fromisoformat(
            latest.created_at.replace("Z", "+00:00")
        )

        elapsed = (now_dt - created_at).total_seconds()

        if elapsed < cooldown_seconds:
            raise VerificationCooldownError(
                "Please wait before requesting another verification email"
            )

    # Keep earlier unexpired tokens usable during resend.
    # Successful verification will revoke all remaining tokens.
    db.refresh(user)

    email = _normalize_email(user.email)
    now = to_db_timestamp(now_dt)

    state = db.get(UserEmailVerification, user.user_id)

    if state is None:
        state = UserEmailVerification(
            user_id=user.user_id,
            email=email,
            verified_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
    elif state.email != email:
        state.email = email
        state.verified_at = None
        state.updated_at = now

    raw_token, digest = generate_verification_token()

    db.add(
        EmailVerificationChallenge(
            challenge_id=str(uuid4()),
            user_id=user.user_id,
            email=email,
            token_digest=digest,
            created_at=now,
            expires_at=to_db_timestamp(
                now_dt + timedelta(hours=TOKEN_TTL_HOURS)
            ),
            consumed_at=None,
        )
    )

    db.flush()
    return raw_token
