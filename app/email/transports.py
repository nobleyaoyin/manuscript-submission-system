"""A provider-neutral interface and an explicitly constructed memory-only sink."""
import re
from threading import Lock
from typing import Protocol

from .types import EmailMessage, SendResult


class EmailTransport(Protocol):
    def send(self, message: EmailMessage, idempotency_key: str) -> SendResult: ...


class InMemoryTransport:
    """Capture messages only in this instance; never send or persist them."""

    def __init__(self) -> None:
        self._messages: dict[str, EmailMessage] = {}
        self._lock = Lock()

    @property
    def messages(self) -> tuple[EmailMessage, ...]:
        with self._lock:
            return tuple(self._messages.values())

    def send(self, message: EmailMessage, idempotency_key: str) -> SendResult:
        if not isinstance(message, EmailMessage):
            raise TypeError("Expected an EmailMessage")
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
            raise ValueError("Invalid idempotency key")
        with self._lock:
            existing = self._messages.get(idempotency_key)
            if existing is not None:
                if existing != message:
                    raise ValueError("Idempotency key is already associated with another message")
                return SendResult(status="duplicate")
            self._messages[idempotency_key] = message
            return SendResult(status="captured")
