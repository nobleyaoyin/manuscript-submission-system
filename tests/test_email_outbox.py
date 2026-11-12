"""Same-transaction outbox helper tests; all SQLite data is disposable."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
import threading
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app import models
from app.email.outbox import (InvitationEligibilityError, OutboxConflictError,
                              enqueue_reviewer_invitation)
from app.email.transports import InMemoryTransport
from tests import test_email_outbox_migration as migration_fixture

STAMP = migration_fixture.STAMP


class OutboxPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = migration_fixture.EmailOutboxMigrationTest(methodName='test_additive_preservation_integrity_and_repeat')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.migrate()
        self.engine = create_engine(f'sqlite:///{self.fixture.path}', connect_args={'timeout': 3, 'check_same_thread': False})
        @event.listens_for(self.engine, 'connect')
        def enable_fk(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.db = self.Session()
        self.addCleanup(self.db.close)

    def test_create_owns_recipient_and_no_commit_or_transport(self):
        with patch.object(self.db, 'commit', side_effect=AssertionError('helper must not commit')), \
             patch.object(self.db, 'rollback', side_effect=AssertionError('helper must not roll back the caller')), \
             patch.object(InMemoryTransport, 'send', side_effect=AssertionError('transport forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = enqueue_reviewer_invitation(self.db, 1)
        ev = self.db.get(models.NotificationEvent, result.event_id)
        out = self.db.get(models.EmailOutbox, result.outbox_id)
        self.assertEqual((ev.event_type, ev.invitation_assignment_id, ev.schema_version), ('reviewer.invitation.created', 1, 1))
        self.assertEqual((out.event_id, out.recipient_user_id, out.template_key, out.template_version), (ev.event_id, 2, 'reviewer_invitation', 'v1'))
        self.assertEqual((out.status, out.attempt_count, out.lease_token, out.lease_until, out.accepted_at), ('pending', 0, None, None, None))
        for stamp in (ev.created_at, out.created_at, out.updated_at, out.next_attempt_at):
            self.assertEqual(datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y-%m-%dT%H:%M:%S.%fZ'), stamp)
        self.assertEqual(self.db.query(models.EmailDeliveryAttempt).count(), 0)
        with sqlite3.connect(self.fixture.path) as other:
            self.assertEqual(other.execute('SELECT count(*) FROM notification_events').fetchone()[0], 0)
            self.assertEqual(other.execute('SELECT count(*) FROM email_outbox').fetchone()[0], 0)
        self.assertTrue(result.created_event and result.created_outbox)
        self.db.rollback()
        with sqlite3.connect(self.fixture.path) as other:
            self.assertEqual(other.execute('SELECT count(*) FROM notification_events').fetchone()[0], 0)
            self.assertEqual(other.execute('SELECT count(*) FROM email_outbox').fetchone()[0], 0)
            self.assertEqual(other.execute('SELECT count(*) FROM notifications').fetchone()[0], 1)

    def test_idempotent_before_and_after_commit(self):
        first = enqueue_reviewer_invitation(self.db, 1)
        second = enqueue_reviewer_invitation(self.db, 1)
        self.assertEqual((first.event_id, first.outbox_id), (second.event_id, second.outbox_id))
        self.assertFalse(second.created_event or second.created_outbox)
        self.db.commit()
        with self.Session() as other:
            third = enqueue_reviewer_invitation(other, 1)
            self.assertEqual((first.event_id, first.outbox_id), (third.event_id, third.outbox_id))
            self.assertFalse(third.created_event or third.created_outbox)
            self.assertEqual(other.query(models.NotificationEvent).count(), 1)
            self.assertEqual(other.query(models.EmailOutbox).count(), 1)

    def test_existing_event_missing_outbox_gets_one_stable_intent(self):
        self.db.add(models.NotificationEvent(event_id='existing-event', event_type='reviewer.invitation.created', invitation_assignment_id=1, created_at=STAMP, schema_version=1))
        self.db.commit()
        result = enqueue_reviewer_invitation(self.db, 1)
        self.assertEqual(result.event_id, 'existing-event')
        self.assertFalse(result.created_event)
        self.assertTrue(result.created_outbox)
        self.assertEqual(enqueue_reviewer_invitation(self.db, 1).outbox_id, result.outbox_id)

    def test_unflushed_invitation_is_visible_with_autoflush_disabled(self):
        self.db.add(models.ReviewerInvitation(assignment_id=2, status='pending'))
        self.db.get(models.ReviewAssignment, 2).status = 'assigned'
        result = enqueue_reviewer_invitation(self.db, 2)
        self.assertEqual(self.db.get(models.EmailOutbox, result.outbox_id).recipient_user_id, 4)
        self.db.rollback()
        self.assertIsNone(self.db.get(models.ReviewerInvitation, 2))

    def test_expected_unique_conflicts_use_savepoints(self):
        import app.email.outbox as module
        first = enqueue_reviewer_invitation(self.db, 1)
        self.db.commit()
        marker = models.Notification(user_id=2, manuscript_id=1, notification_type='synthetic', message='caller work', is_read=False)
        self.db.add(marker)
        original_event, original_outboxes = module._event, module._outboxes
        event_calls, outbox_calls = [], []
        def event_lookup(db, assignment_id):
            event_calls.append(1)
            return None if len(event_calls) == 1 else original_event(db, assignment_id)
        def outbox_lookup(db, event_id):
            outbox_calls.append(1)
            return [] if len(outbox_calls) == 1 else original_outboxes(db, event_id)
        with patch.object(module, '_event', event_lookup), patch.object(module, '_outboxes', outbox_lookup):
            result = enqueue_reviewer_invitation(self.db, 1)
        self.assertEqual((result.event_id, result.outbox_id), (first.event_id, first.outbox_id))
        self.assertFalse(result.created_event or result.created_outbox)
        self.assertEqual(self.db.query(models.Notification).count(), 2)
        self.db.rollback()
        self.assertEqual(self.db.query(models.Notification).count(), 1)

    def test_conflicting_recipient_and_template_are_rejected(self):
        self.db.add(models.NotificationEvent(event_id='existing-event', event_type='reviewer.invitation.created', invitation_assignment_id=1, created_at=STAMP, schema_version=1))
        self.db.flush()
        existing = models.EmailOutbox(outbox_id='wrong', event_id='existing-event', recipient_user_id=4, template_key='reviewer_invitation', template_version='v1', status='pending', attempt_count=0, next_attempt_at=STAMP, created_at=STAMP, updated_at=STAMP)
        self.db.add(existing)
        self.db.commit()
        with self.assertRaises(OutboxConflictError):
            enqueue_reviewer_invitation(self.db, 1)
        self.db.rollback()
        existing = self.db.get(models.EmailOutbox, 'wrong')
        existing.recipient_user_id = 2
        existing.template_version = 'v2'
        self.db.commit()
        with self.assertRaises(OutboxConflictError):
            enqueue_reviewer_invitation(self.db, 1)

    def test_invalid_missing_and_ineligible_records(self):
        for assignment_id in (0, -1, None, True, '1', 999, 2):
            with self.subTest(assignment_id=assignment_id), self.assertRaises(InvitationEligibilityError):
                enqueue_reviewer_invitation(self.db, assignment_id)
        for field, value in [('status', 'declined'), ('status', 'accepted')]:
            invitation = self.db.get(models.ReviewerInvitation, 1)
            setattr(invitation, field, value)
            with self.subTest(value=value), self.assertRaises(InvitationEligibilityError):
                enqueue_reviewer_invitation(self.db, 1)
            self.db.rollback()
        manuscript = self.db.get(models.Manuscript, 1)
        manuscript.status = 'draft'
        with self.assertRaises(InvitationEligibilityError): enqueue_reviewer_invitation(self.db, 1)
        self.db.rollback()
        manuscript = self.db.get(models.Manuscript, 1)
        manuscript.review_round = 2
        with self.assertRaises(InvitationEligibilityError): enqueue_reviewer_invitation(self.db, 1)
        self.db.rollback()
        assignment = self.db.get(models.ReviewAssignment, 1)
        assignment.status = 'completed'
        with self.assertRaises(InvitationEligibilityError): enqueue_reviewer_invitation(self.db, 1)
        self.db.rollback()
        reviewer = self.db.get(models.User, 2)
        reviewer.role = 'author'
        with self.assertRaises(InvitationEligibilityError): enqueue_reviewer_invitation(self.db, 1)
        self.db.rollback()
        self.assertEqual(self.db.query(models.NotificationEvent).count(), 0)

    def test_unrelated_integrity_error_preserves_caller_transaction(self):
        self.db.add(models.ReviewerInvitation(assignment_id=2, status='pending'))
        self.db.flush()
        self.db.add(models.NotificationEvent(event_id='collision', event_type='reviewer.invitation.created', invitation_assignment_id=2, created_at=STAMP, schema_version=1))
        self.db.commit()
        marker = models.Notification(user_id=2, manuscript_id=1, notification_type='synthetic', message='caller work', is_read=False)
        self.db.add(marker)
        with patch('app.email.outbox.uuid4', return_value='collision'):
            with self.assertRaises(IntegrityError): enqueue_reviewer_invitation(self.db, 1)
        self.assertIsNotNone(marker.notification_id)
        self.assertEqual(self.db.query(models.Notification).count(), 2)
        self.db.rollback()
        self.assertEqual(self.db.query(models.Notification).count(), 1)
        self.assertEqual(self.db.query(models.NotificationEvent).count(), 1)

    def test_foreign_keys_uniqueness_and_no_backfill(self):
        self.assertEqual(self.db.query(models.NotificationEvent).count(), 0)
        self.assertEqual(self.db.query(models.EmailOutbox).count(), 0)
        result = enqueue_reviewer_invitation(self.db, 1)
        with self.assertRaises(IntegrityError):
            with self.db.begin_nested():
                self.db.add(models.NotificationEvent(event_id='duplicate', event_type='reviewer.invitation.created', invitation_assignment_id=1, created_at=STAMP, schema_version=1))
                self.db.flush()
        with self.assertRaises(IntegrityError):
            with self.db.begin_nested():
                self.db.add(models.EmailOutbox(outbox_id='bad', event_id=result.event_id, recipient_user_id=9999, template_key='reviewer_invitation', template_version='v1', status='pending', attempt_count=0, next_attempt_at=STAMP, created_at=STAMP, updated_at=STAMP))
                self.db.flush()
        self.assertEqual(self.db.query(models.Notification).count(), 1)

    def test_concurrent_same_invitation_and_caller_retry(self):
        import app.email.outbox as module
        barrier = threading.Barrier(2)
        local = threading.local()
        original = module._event
        def synchronized(db, aid):
            result = original(db, aid)
            if not getattr(local, 'waited', False):
                local.waited = True
                barrier.wait(timeout=10)
            return result
        def worker(_):
            with self.Session() as db:
                try:
                    result = enqueue_reviewer_invitation(db, 1)
                    db.commit()
                    return ('ok', result.event_id, result.outbox_id)
                except OperationalError as error:
                    db.rollback()
                    return ('locked', str(error.orig))
        with patch.object(module, '_event', synchronized):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(worker, range(2)))
        successes = [result for result in results if result[0] == 'ok']
        self.assertGreaterEqual(len(successes), 1, results)
        for result in results:
            if result[0] == 'locked': self.assertIn('locked', result[1].lower())
        with self.Session() as retry:
            result = enqueue_reviewer_invitation(retry, 1)
            self.assertEqual((result.event_id, result.outbox_id), successes[0][1:])
            self.assertEqual(retry.query(models.NotificationEvent).count(), 1)
            self.assertEqual(retry.query(models.EmailOutbox).count(), 1)


if __name__ == '__main__':
    unittest.main()
