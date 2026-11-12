"""Isolated reviewer invitation template tests; no database or delivery."""
from dataclasses import fields
import unittest
from unittest.mock import patch

from app.email.templates import render_template
from app.email.transports import InMemoryTransport


class ReviewerInvitationTemplateTest(unittest.TestCase):
    def render(self, recipient='reviewer@example.com', context=None):
        return render_template('reviewer_invitation', 'v1', recipient, {} if context is None else context)

    def test_template_identity_subject_and_plain_text(self):
        message = self.render()
        self.assertEqual((message.template_key, message.template_version), ('reviewer_invitation', 'v1'))
        self.assertEqual(message.subject, 'G-Sub — Invitation to Review a Manuscript')
        self.assertEqual(message.text_body, (
            'Dear Reviewer,\n\n'
            'A manuscript review invitation is available in G-Sub. '
            'Please sign in to G-Sub to review the invitation and '
            'choose whether to accept or decline.\n\n'
            'Thank you for considering this request.\n\n'
            'G-Sub Editorial Team'
        ))
        self.assertIn('Dear Reviewer', message.text_body)
        self.assertIn('review invitation', message.text_body)
        self.assertIn('sign in to G-Sub', message.text_body)
        self.assertIn('accept or decline', message.text_body)
        self.assertIsNone(message.html_body)
        self.assertNotIn('download', message.text_body.lower())
        self.assertNotIn('deadline', message.text_body.lower())

    def test_static_content_and_no_attachment_or_confidential_fields(self):
        message = self.render()
        self.assertEqual({field.name for field in fields(message)}, {'recipient','subject','text_body','html_body','template_key','template_version'})
        self.assertFalse(hasattr(message, 'attachments'))
        content = message.subject + message.text_body
        for value in ('Synthetic Manuscript Title', 'Synthetic Abstract', 'Reviewer Two', 'Synthetic Author', 'Synthetic Review Comment', '/private/uploads', 'synthetic-jwt', 'synthetic-password'):
            self.assertNotIn(value, content)

    def test_unsafe_context_and_urls_cannot_be_interpolated(self):
        for key, value in [('application_url','javascript:alert(1)'), ('application_url','http://example.com'),
                           ('application_url','https://user:pass@example.com'), ('application_url','https://example.com/redirect?to=elsewhere'),
                           ('origin','https://example.com\r\nBcc:other@example.com'), ('title','<script>private</script>'),
                           ('reviewer','Another Reviewer'), ('deadline','tomorrow'), ('token','synthetic-jwt')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Unexpected template context') as error:
                self.render(context={key:value})
            self.assertNotIn(value, str(error.exception))
        text = self.render().text_body.lower()
        for token in ('https://', 'http://', 'javascript:', 'href=', 'redirect'):
            self.assertNotIn(token, text)

    def test_recipient_validation_and_special_characters(self):
        message = self.render(recipient='reviewer+tag@example.com')
        self.assertEqual(message.recipient, 'reviewer+tag@example.com')
        self.assertNotIn('reviewer+tag', message.subject + message.text_body)
        for recipient in ('reviewer@example.com\r\nBcc:other@example.com', 'reviewer@example.com\u2028Bcc:other@example.com', 'not-an-address'):
            with self.subTest(recipient=recipient), self.assertRaises(ValueError): self.render(recipient=recipient)

    def test_unknown_template_version_and_context_type(self):
        with self.assertRaisesRegex(ValueError, 'Unknown template'):
            render_template('unknown', 'v1', 'reviewer@example.com', {})
        with self.assertRaisesRegex(ValueError, 'Unsupported template version'):
            render_template('reviewer_invitation', 'v2', 'reviewer@example.com', {})
        with self.assertRaisesRegex(ValueError, 'Unexpected template context'):
            render_template('reviewer_invitation', 'v1', 'reviewer@example.com', [])

    def test_render_is_deterministic_without_database_network_or_transport(self):
        with patch('sqlite3.connect', side_effect=AssertionError('database forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
             patch.object(InMemoryTransport, 'send', side_effect=AssertionError('delivery forbidden')):
            self.assertEqual(self.render(), self.render())

    def test_synthetic_template_remains_unchanged(self):
        result = render_template('synthetic_test', 'v1', 'reader@example.com', {})
        self.assertEqual(result.subject, 'G-Sub synthetic test')
        self.assertEqual(result.text_body, 'This is a synthetic test message. No email has been delivered.')


if __name__ == '__main__':
    unittest.main()
