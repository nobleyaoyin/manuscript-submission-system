"""Claim tests against a migrated disposable database and synthetic records."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
import unittest
from unittest.mock import patch

from app.email.worker_store import claim_next, recover_expired, renew_lease
from tests.test_email_outbox_migration import EmailOutboxMigrationTest

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
DURATION = timedelta(seconds=30)


class ClaimLeaseTest(unittest.TestCase):
    def setUp(self):
        self.fixture = EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.migrate()
        self.db = self.connect()
        self.addCleanup(self.db.close)
        self.fixture.insert_event(self.db)
        self.fixture.insert_outbox(self.db)
        self.db.commit()

    def connect(self, timeout=0.1):
        db = sqlite3.connect(self.fixture.path, timeout=timeout)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def claim(self, db=None, now=NOW):
        return claim_next(db or self.db, lease_duration=DURATION, now=now)

    def state(self):
        return self.db.execute('SELECT status,next_attempt_at,lease_token,lease_until FROM email_outbox WHERE outbox_id=?', ('out-1',)).fetchone()

    def test_claim_committed_lease_and_canonical_times(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            lease = self.claim()
        self.assertEqual(lease.outbox_id, 'out-1')
        self.assertTrue(len(lease.token) >= 32)
        self.assertEqual(lease.expires_at, '2026-02-01T00:00:30.000000Z')
        self.assertEqual(self.state(), ('processing', None, lease.token, lease.expires_at))
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0], 0)
        self.db.commit()
        with self.connect() as other:
            self.assertEqual(other.execute('SELECT status FROM email_outbox').fetchone()[0], 'processing')

    def test_caller_rollback_undoes_claim(self):
        self.claim()
        self.db.rollback()
        self.assertEqual(self.state()[:2], ('pending', '2026-01-02T03:04:05.000000Z'))

    def test_only_due_pending_and_retry_wait_are_eligible(self):
        for status, due, eligible in [('pending', '2026-02-02T00:00:00.000000Z', False),
                                     ('retry_wait', '2026-02-02T00:00:00.000000Z', False),
                                     ('retry_wait', '2026-02-01T00:00:00.000000Z', True),
                                     ('blocked_verification', None, False), ('unknown', None, False),
                                     ('failed', None, False), ('cancelled', None, False)]:
            with self.subTest(status=status, due=due):
                self.db.execute('UPDATE email_outbox SET status=?,next_attempt_at=?', (status,due))
                self.db.commit()
                self.assertEqual(self.claim() is not None, eligible)
                self.db.rollback()

    def test_existing_unresolved_or_accepted_attempt_prevents_claim(self):
        for outcome, finished in [('started', None), ('unknown', '2026-01-02T03:04:05.000000Z'),
                                  ('accepted', '2026-01-02T03:04:05.000000Z'), ('permanent', '2026-01-02T03:04:05.000000Z')]:
            with self.subTest(outcome=outcome):
                self.fixture.insert_attempt(self.db, outcome=outcome, finished=finished)
                self.db.commit()
                self.assertIsNone(self.claim())
                self.db.rollback()
                self.db.execute('DELETE FROM email_delivery_attempts')
                self.db.commit()

    def test_retryable_attempt_permits_claim(self):
        self.fixture.insert_attempt(self.db, outcome='retryable', finished='2026-01-02T03:04:05.000000Z')
        self.db.commit()
        self.assertIsNotNone(self.claim())
        self.db.rollback()

    def test_expired_without_attempt_recovers_and_reclaims(self):
        first = self.claim(); self.db.commit()
        self.assertIsNone(recover_expired(self.db, now=NOW + timedelta(seconds=29)))
        self.db.commit()
        result = recover_expired(self.db, now=NOW + DURATION)
        self.assertEqual((result.outbox_id,result.status), ('out-1','pending'))
        self.db.commit()
        second = self.claim(now=NOW + DURATION); self.db.commit()
        self.assertNotEqual(first.token, second.token)
        self.assertIsNone(renew_lease(self.db, first, lease_duration=DURATION, now=NOW + DURATION))
        self.db.commit()
        self.assertEqual(self.state()[2], second.token)

    def test_started_attempt_becomes_unknown_without_retry(self):
        lease = self.claim(); self.db.commit()
        self.db.execute("INSERT INTO email_delivery_attempts(attempt_id,outbox_id,attempt_number,lease_token,started_at,outcome) VALUES ('a','out-1',1,?,?,'started')", (lease.token,'2026-02-01T00:00:00.000000Z'))
        self.db.commit()
        result = recover_expired(self.db, now=NOW + DURATION)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(self.db.execute('SELECT outcome,finished_at FROM email_delivery_attempts').fetchone(), ('unknown','2026-02-01T00:00:30.000000Z'))
        self.db.commit()
        self.assertIsNone(self.claim(now=NOW + timedelta(days=1)))
        self.db.rollback()

    def test_recovery_unknown_for_inconsistent_accepted_ledger(self):
        self.claim(); self.db.commit()
        self.fixture.insert_attempt(self.db, outcome='accepted', finished='2026-01-02T03:04:05.000000Z')
        self.db.commit()
        self.assertEqual(recover_expired(self.db, now=NOW + DURATION).status, 'unknown')
        self.db.rollback()
        self.assertEqual(self.state()[0], 'processing')

    def test_recovery_rollback_restores_attempt_and_lease(self):
        lease = self.claim(); self.db.commit()
        self.db.execute("INSERT INTO email_delivery_attempts(attempt_id,outbox_id,attempt_number,lease_token,started_at,outcome) VALUES ('a','out-1',1,?,?,'started')", (lease.token,'2026-02-01T00:00:00.000000Z'))
        self.db.commit()
        recover_expired(self.db, now=NOW + DURATION)
        self.db.rollback()
        self.assertEqual(self.state()[0], 'processing')
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'started')

    def test_current_and_expired_lease_fencing(self):
        lease = self.claim(); self.db.commit()
        renewed = renew_lease(self.db, lease, lease_duration=timedelta(seconds=60), now=NOW + timedelta(seconds=1))
        self.assertEqual(renewed.expires_at, '2026-02-01T00:01:01.000000Z')
        self.db.commit()
        self.assertIsNone(renew_lease(self.db, renewed, lease_duration=DURATION, now=NOW + timedelta(seconds=61)))
        self.db.commit()
        self.assertEqual(self.state()[3], renewed.expires_at)

    def test_two_connections_cannot_claim_same_item(self):
        barrier = threading.Barrier(2)
        def run(_):
            with self.connect(timeout=1) as db:
                barrier.wait(timeout=5)
                result = self.claim(db)
                db.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_busy_lock_propagates_without_retry(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.connect(timeout=0.01) as other:
            with self.assertRaises(sqlite3.OperationalError):
                self.claim(other)
            self.assertFalse(other.in_transaction)
        self.db.rollback()
        self.assertEqual(self.state()[0], 'pending')

    def test_validation_and_no_nested_transaction(self):
        with self.assertRaises(ValueError): claim_next(self.db, lease_duration=DURATION, now=datetime(2026,1,1))
        with self.assertRaises(ValueError): claim_next(self.db, lease_duration=timedelta(0), now=NOW)
        self.db.execute('BEGIN')
        with self.assertRaises(ValueError): self.claim()
        self.db.rollback()
        self.db.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaises(ValueError): self.claim()
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=1001')
        with self.assertRaises(ValueError): self.claim()

    def test_no_automatic_commit_on_empty_claim(self):
        self.db.execute("UPDATE email_outbox SET status='cancelled', next_attempt_at=NULL")
        self.db.commit()
        self.assertIsNone(self.claim())
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()


if __name__ == '__main__':
    unittest.main()
