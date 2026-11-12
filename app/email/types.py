"""Validated provider-neutral message and transport result types."""
from dataclasses import dataclass, field
import re
import unicodedata

from email_validator import EmailNotValidError, validate_email


def _header(value: str, name: str, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"Invalid {name}")
    if any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value):
        raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class EmailMessage:
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text_body: str = field(repr=False)
    template_key: str
    template_version: str
    html_body: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _header(self.recipient, "recipient", 254)
        try:
            # Syntax only: never query DNS or verify a real mailbox.
            validated = validate_email(self.recipient, check_deliverability=False)
        except EmailNotValidError:
            raise ValueError("Invalid recipient") from None
        object.__setattr__(self, "recipient", validated.normalized)
        _header(self.subject, "subject", 255)
        for value, name in ((self.template_key, "template key"), (self.template_version, "template version")):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value):
                raise ValueError(f"Invalid {name}")
        if not isinstance(self.text_body, str) or not self.text_body.strip():
            raise ValueError("Invalid text body")
        if self.html_body is not None and (not isinstance(self.html_body, str) or not self.html_body.strip()):
            raise ValueError("Invalid HTML body")


@dataclass(frozen=True)
class SendResult:
    """A sink outcome, not evidence of provider or inbox delivery."""
    status: str
