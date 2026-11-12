"""Network-free unit tests for the isolated synthetic email infrastructure."""
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.email.templates import render_template
from app.email.transports import EmailTransport, InMemoryTransport
from app.email.types import EmailMessage, SendResult


def message(**changes):
    fields = {
        "recipient": "reader@example.com",
        "subject": "Synthetic test",
        "text_body": "Synthetic content only.",
        "template_key": "synthetic_test",
        "template_version": "v1",
    }
    fields.update(changes)
    return EmailMessage(**fields)


class EmailInfrastructureTests(unittest.TestCase):
    def test_valid_message_and_optional_html(self):
        result = message(html_body="<p>Synthetic</p>")
        self.assertEqual(result.recipient, "reader@example.com")
        self.assertEqual(result.html_body, "<p>Synthetic</p>")
        with self.assertRaises(ValueError):
            message(text_body=" ")
        with self.assertRaises(ValueError):
            message(html_body=" ")

    def test_invalid_recipients(self):
        for address in ("", "not-an-email", "a@", "a@bad domain.com", "a@example..com", "A <a@example.com>", None):
            with self.subTest(address=address), self.assertRaises(ValueError):
                message(recipient=address)

    def test_subject_required(self):
        for subject in ("", "   ", None):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                message(subject=subject)

    def test_header_injection_rejected(self):
        for field, value in (("subject", "Hello\r\nBcc: other@example.com"),
                             ("subject", "Hello\u2028Bcc: other@example.com"),
                             ("recipient", "reader@example.com\nBcc: other@example.com")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                message(**{field: value})

    def test_template_renders_only_static_synthetic_text(self):
        rendered = render_template("synthetic_test", "v1", "reader@example.com", {})
        self.assertEqual(rendered.template_key, "synthetic_test")
        self.assertEqual(rendered.template_version, "v1")
        self.assertEqual(rendered.subject, "G-Sub synthetic test")
        self.assertIn("No email has been delivered", rendered.text_body)
        self.assertIsNone(rendered.html_body)

    def test_unknown_template_and_version(self):
        with self.assertRaisesRegex(ValueError, "Unknown template"):
            render_template("other", "v1", "reader@example.com", {})
        with self.assertRaisesRegex(ValueError, "Unsupported template version"):
            render_template("synthetic_test", "v2", "reader@example.com", {})

    def test_template_rejects_extra_or_invalid_context(self):
        for context in ({"comment": "synthetic confidential content"}, {"password": "synthetic secret"}, None, []):
            with self.subTest(context_type=type(context)), self.assertRaises(ValueError) as caught:
                render_template("synthetic_test", "v1", "reader@example.com", context)
            self.assertNotIn("synthetic", str(caught.exception))

    def test_memory_capture_and_transport_result(self):
        sink: EmailTransport = InMemoryTransport()
        item = message()
        self.assertEqual(sink.send(item, "event-1"), SendResult(status="captured"))
        self.assertEqual(sink.messages, (item,))
        self.assertIsInstance(sink.messages, tuple)

    def test_idempotency_and_conflicting_reuse(self):
        sink = InMemoryTransport()
        item = message()
        self.assertEqual(sink.send(item, "event-1").status, "captured")
        self.assertEqual(sink.send(item, "event-1").status, "duplicate")
        self.assertEqual(len(sink.messages), 1)
        with self.assertRaises(ValueError):
            sink.send(message(subject="Different"), "event-1")
        for key in ("", "bad\nkey", "key with space", "x" * 129, None):
            with self.subTest(key_type=type(key)), self.assertRaises(ValueError):
                sink.send(item, key)
        self.assertEqual(len(sink.messages), 1)
        self.assertEqual(sink.send(item, "event-2").status, "captured")
        self.assertEqual(len(sink.messages), 2)

    def test_no_network_during_validation_render_and_capture(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            item = render_template("synthetic_test", "v1", "reader@example.com", {})
            self.assertEqual(InMemoryTransport().send(item, "safe-1").status, "captured")

    def test_sink_performs_no_file_io(self):
        item = message()
        sink = InMemoryTransport()
        with patch("builtins.open", side_effect=AssertionError("file I/O forbidden")):
            self.assertEqual(sink.send(item, "safe-1").status, "captured")
            self.assertEqual(sink.messages, (item,))

    def test_concurrent_idempotency_within_one_sink(self):
        item = message()
        sink = InMemoryTransport()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: sink.send(item, "same-key").status, range(20)))
        self.assertEqual(results.count("captured"), 1)
        self.assertEqual(results.count("duplicate"), 19)
        self.assertEqual(sink.messages, (item,))

    def test_import_does_not_start_delivery_or_network(self):
        code = '''
from unittest.mock import patch
with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
    import app.email
    import app.email.types
    import app.email.templates
    import app.email.transports
    assert app.email.transports.InMemoryTransport().messages == ()
'''
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_representations_and_errors_do_not_echo_message_content(self):
        item = message(recipient="reader@example.com", subject="synthetic-secret", text_body="synthetic-confidential", html_body="<p>synthetic-private</p>")
        for text in (repr(item), repr(InMemoryTransport()), repr(SendResult("captured"))):
            for secret in ("reader@example.com", "synthetic-secret", "synthetic-confidential", "synthetic-private"):
                self.assertNotIn(secret, text)
        with self.assertRaises(ValueError) as caught:
            message(recipient="synthetic-secret invalid")
        self.assertNotIn("synthetic-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
