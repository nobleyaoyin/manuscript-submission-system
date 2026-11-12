"""Resend email delivery adapter for local development."""

import os

import resend

from app.config import IS_DEMO
from app.email.types import EmailMessage


class EmailDeliveryError(RuntimeError):
    """Email delivery could not be completed."""


def send_email(message: EmailMessage) -> str:
    """Send a real email through Resend.

    Returns the provider email ID.
    Does not log or persist the email body.
    """
    if IS_DEMO:
        raise EmailDeliveryError("Email delivery is disabled in demo mode")

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise EmailDeliveryError("RESEND_API_KEY is not configured")

    # Until a sender domain is verified, restrict delivery to
    # the explicitly configured development test recipient.
    allowed_recipient = os.environ.get(
        "GSUB_TEST_EMAIL", ""
    ).strip().lower()

    if not allowed_recipient or message.recipient.lower() != allowed_recipient:
        raise EmailDeliveryError(
            "Recipient is not authorized for development email testing"
        )

    resend.api_key = api_key

    try:
        result = resend.Emails.send({
            "from": "G-Sub <onboarding@resend.dev>",
            "to": [message.recipient],
            "subject": message.subject,
            "text": message.text_body,
        })
    except Exception as exc:
        raise EmailDeliveryError("Resend delivery request failed") from exc

    email_id = result.get("id")
    if not email_id:
        raise EmailDeliveryError("Resend did not return an email ID")

    return email_id
