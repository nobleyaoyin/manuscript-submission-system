"""Create and optionally deliver registration verification emails."""

import os

from sqlalchemy.orm import Session

from app.config import IS_DEMO
from app.models import User
from app.email.verification_service import create_verification_challenge
from app.email.verification_template import build_verification_email
from app.email.resend_transport import send_email, EmailDeliveryError


def prepare_registration_verification(
    db: Session,
    user: User,
) -> object:
    """Create a verification challenge without committing or sending."""
    return create_verification_challenge(db, user)


def deliver_registration_verification(
    recipient: str,
    token: str,
) -> bool:
    """Deliver only to the configured development test recipient.

    Returns False when delivery is disabled or unsuccessful.
    """
    if IS_DEMO:
        return False

    allowed = os.environ.get("GSUB_TEST_EMAIL", "").strip().lower()

    if not allowed or recipient.strip().lower() != allowed:
        return False

    message = build_verification_email(
        recipient=recipient,
        token=token,
    )

    try:
        send_email(message)
    except EmailDeliveryError:
        return False

    return True
