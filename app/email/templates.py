"""Versioned plain-text templates with no dynamic workflow content."""
from collections.abc import Mapping

from .types import EmailMessage


def render_template(key: str, version: str, recipient: str, context: Mapping) -> EmailMessage:
    if key not in ("synthetic_test", "reviewer_invitation"):
        raise ValueError("Unknown template")
    if version != "v1":
        raise ValueError("Unsupported template version")
    if not isinstance(context, Mapping) or len(context) != 0:
        raise ValueError("Unexpected template context")
    if key == "reviewer_invitation":
        return EmailMessage(
            recipient=recipient,
            subject="G-Sub — Invitation to Review a Manuscript",
            text_body=(
                "Dear Reviewer,\n\n"
                "A manuscript review invitation is available in G-Sub. "
                "Please sign in to G-Sub to review the invitation and "
                "choose whether to accept or decline.\n\n"
                "Thank you for considering this request.\n\n"
                "G-Sub Editorial Team"
            ),
            template_key=key,
            template_version=version,
        )
    return EmailMessage(
        recipient=recipient,
        subject="G-Sub synthetic test",
        text_body="This is a synthetic test message. No email has been delivered.",
        template_key=key,
        template_version=version,
    )
