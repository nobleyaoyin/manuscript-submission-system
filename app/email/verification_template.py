"""Email verification message template."""

from urllib.parse import quote

from app.email.types import EmailMessage


def build_verification_email(
    recipient: str,
    token: str,
    frontend_origin: str = "http://localhost:5173",
) -> EmailMessage:
    """Build an email containing a 24-hour verification link."""

    if frontend_origin != "http://localhost:5173":
        raise ValueError("Unsupported development frontend origin")

    if not isinstance(token, str) or not token:
        raise ValueError("Invalid verification token")

    verification_url = (
        f"{frontend_origin}/verify-email"
        f"?token={quote(token, safe='')}"
    )

    return EmailMessage(
        recipient=recipient,
        subject="G-Sub — Verify Your Email Address",
        text_body=(
            "Welcome to G-Sub!\n\n"
            "Please verify your email address by opening "
            "the following link:\n\n"
            f"{verification_url}\n\n"
            "This verification link expires in 24 hours "
            "and can only be used once.\n\n"
            "If you did not create a G-Sub account, "
            "you can safely ignore this email.\n\n"
            "G-Sub Team"
        ),
        template_key="email_verification",
        template_version="v1",
    )
