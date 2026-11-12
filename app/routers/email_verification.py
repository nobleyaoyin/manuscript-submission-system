"""Email verification endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.users import get_current_user, normalize_email, limit_login
from app.schemas import UserLogin
from app.security import verify_password
from fastapi import Request
from app.email.verification_service import (
    consume_verification_token,
    is_email_verified,
)

router = APIRouter(
    prefix="/users",
    tags=["Email Verification"],
)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


@router.post("/verify-email")
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
):
    try:
        verified = consume_verification_token(db, payload.token)

        if not verified:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token",
            )

        db.commit()

    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise

    return {"message": "Email verified successfully"}


@router.get("/email-verification-status")
def email_verification_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return {
        "email": current_user.email,
        "verified": is_email_verified(db, current_user),
    }


@router.post("/resend-verification")
def resend_verification(
    credentials: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    """Request a verification email using account credentials."""
    from app.config import IS_DEMO
    from app.email.verification_service import (
        create_resend_challenge,
        VerificationCooldownError,
    )
    from app.email.registration_verification import (
        deliver_registration_verification,
    )

    if IS_DEMO:
        raise HTTPException(
            status_code=403,
            detail="Email verification is disabled in demo mode",
        )

    limit_login(request)

    email = normalize_email(str(credentials.email))
    current_user = db.scalar(
        select(User).where(User.email == email)
    )

    if (
        current_user is None
        or not verify_password(
            credentials.password,
            current_user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if is_email_verified(db, current_user):
        return {"message": "Email is already verified", "sent": False}

    try:
        token = create_resend_challenge(db, current_user)
        db.commit()

    except VerificationCooldownError:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="Please wait before requesting another verification email",
            headers={"Retry-After": "60"},
        )

    except Exception:
        db.rollback()
        raise

    delivered = deliver_registration_verification(
        recipient=current_user.email,
        token=token,
    )

    if not delivered:
        return {
            "message": "Verification email could not be sent",
            "sent": False,
        }

    return {
        "message": "Verification email sent",
        "sent": True,
    }
