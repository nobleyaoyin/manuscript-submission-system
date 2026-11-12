"""Attempt-state tests use only synthetic records and temporary SQLite files."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
import unittest
from unittest.mock import patch

from app.email.attempt_state import (Attempt, FakeTransport, Outcome, finish_attempt,
                                     simulate_attempt, start_attempt)
from app.email.worker_store import Lease, claim_next, recover_expired
from tests import test_email_outbox_migration as fixture_module

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
WAIT = timedelta(seconds=10)


class AmbiguousConnection(sqlite3.Connection):
    fail_commit = False
    commit_then_fail = False

    def commit(self):
        if self.fail_commit:
            if self.commit_then_fail:
                super().commit()
            raise sqlite3.OperationalError('synthetic commit failure')
        return super().commit()


class AttemptStateTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture_module.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.fx.migrate()
        self.db = self.connect()
        self.addCleanup(self.db.close)
        self.fx.insert_event(self.db); self.fx.insert_outbox(self.db); self.db.commit()
        self.lease = claim_next(self.db, lease_duration=timedelta(minutes=2), now=NOW)
        self.db.commit()

    def connect(self, factory=sqlite3.Connection):
        db = sqlite3.connect(self.fx.path, timeout=0.5, factory=factory)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def start(self, db=None, lease=None, now=NOW, limit=3):
        return start_attempt(db or self.db, lease or self.lease, max_attempts=limit, now=now)

    def finish(self, attempt, outcome, now=NOW, limit=3, lease=None):
        return finish_attempt(self.db, lease or self.lease, attempt, outcome,
                              max_attempts=limit, retry_delay=WAIT, now=now)

    def state(self):
        return self.db.execute('SELECT status,attempt_count,next_attempt_at,lease_token FROM email_outbox').fetchone()

    def test_start_durable_only_after_caller_commit(self):
        attempt = self.start()
        self.assertEqual(attempt.number, 1)
        with self.connect() as other:
            self.assertEqual(other.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0], 0)
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT outcome,attempt_number FROM email_delivery_attempts').fetchone(), ('started',1))

    def test_rollback_start_removes_attempt_and_counter(self):
        self.start(); self.db.rollback()
        self.assertEqual(self.state()[1], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0], 0)

    def test_no_duplicate_active_attempt(self):
        self.start(); self.db.commit()
        with self.assertRaises(ValueError): self.start()
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0], 1)

    def test_concurrent_start_one_winner(self):
        barrier = threading.Barrier(2)
        def run(_):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:
                    result = self.start(db=db); db.commit(); return result.number
                except ValueError:
                    db.rollback(); return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(run, range(2)))
        self.assertEqual(sorted(x for x in result if x is not None), [1])

    def test_accepted_is_provider_acceptance_only(self):
        attempt = self.start(); self.db.commit()
        self.assertEqual(self.finish(attempt, Outcome.ACCEPTED), 'accepted'); self.db.commit()
        self.assertEqual(self.state()[:2], ('accepted',1))
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'accepted')

    def test_retry_number_due_and_limit(self):
        attempt = self.start(limit=2); self.db.commit()
        self.assertEqual(self.finish(attempt, Outcome.RETRYABLE, limit=2), 'retry_wait'); self.db.commit()
        self.assertEqual(self.state()[2], '2026-02-01T00:00:10.000000Z')
        self.assertIsNone(claim_next(self.db, lease_duration=timedelta(minutes=2), now=NOW)); self.db.commit()
        self.lease = claim_next(self.db, lease_duration=timedelta(minutes=2), now=NOW+WAIT); self.db.commit()
        attempt = self.start(now=NOW+WAIT, limit=2); self.db.commit()
        self.assertEqual(attempt.number, 2)
        self.assertEqual(self.finish(attempt, Outcome.RETRYABLE, now=NOW+WAIT, limit=2), 'failed'); self.db.commit()
        self.assertEqual(self.state()[:2], ('failed',2))

    def test_permanent_and_unknown_never_retry(self):
        for outcome, expected in ((Outcome.PERMANENT,'failed'),(Outcome.UNKNOWN,'unknown')):
            with self.subTest(outcome=outcome):
                self.db.execute('SAVEPOINT case_one')
                self.db.execute('RELEASE case_one')
                attempt = self.start(); self.db.commit()
                self.assertEqual(self.finish(attempt,outcome),expected); self.db.commit()
                self.assertIsNone(claim_next(self.db, lease_duration=WAIT, now=NOW+timedelta(days=2))); self.db.commit()
                if outcome is Outcome.PERMANENT:
                    self.db.execute('DELETE FROM email_delivery_attempts')
                    self.db.execute("UPDATE email_outbox SET status='processing',attempt_count=0,lease_token=?,lease_until=?", (self.lease.token,self.lease.expires_at))
                    self.db.commit()

    def test_lease_expiration_fences_start_and_finish(self):
        with self.assertRaises(ValueError): self.start(now=NOW+timedelta(minutes=2))
        self.db.rollback()
        attempt = self.start(); self.db.commit()
        with self.assertRaises(ValueError): self.finish(attempt,Outcome.ACCEPTED,now=NOW+timedelta(minutes=2))
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'started')

    def test_old_lease_cannot_start_or_finish_after_reclaim(self):
        old = self.lease
        recover_expired(self.db, now=NOW+timedelta(minutes=2)); self.db.commit()
        self.lease = claim_next(self.db, lease_duration=timedelta(minutes=2), now=NOW+timedelta(minutes=2)); self.db.commit()
        with self.assertRaises(ValueError): self.start(lease=old,now=NOW+timedelta(minutes=2))
        self.db.rollback()
        attempt = self.start(now=NOW+timedelta(minutes=2)); self.db.commit()
        forged = Attempt(attempt.attempt_id, attempt.outbox_id, attempt.number, old.token)
        with self.assertRaises(ValueError): self.finish(forged,Outcome.ACCEPTED,lease=old,now=NOW+timedelta(minutes=2))
        self.db.rollback()
        self.assertEqual(self.state()[3], self.lease.token)

    def test_crash_after_started_recovers_unknown(self):
        self.start(); self.db.commit()
        self.assertEqual(recover_expired(self.db, now=NOW+timedelta(minutes=2)).status,'unknown'); self.db.commit()
        self.assertIsNone(claim_next(self.db,lease_duration=WAIT,now=NOW+timedelta(minutes=3))); self.db.commit()

    def test_fake_called_after_commit_without_network(self):
        fake = FakeTransport(Outcome.ACCEPTED)
        original = FakeTransport.simulate
        def checked(obj):
            self.assertFalse(self.db.in_transaction)
            with self.connect() as other:
                self.assertEqual(other.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'started')
            return original(obj)
        with patch.object(FakeTransport,'simulate',checked), patch('socket.socket',side_effect=AssertionError('network forbidden')):
            status = simulate_attempt(self.db,self.lease,fake,max_attempts=3,retry_delay=WAIT,clock=lambda: NOW)
        self.assertEqual((status,fake.calls),('accepted',1))

    def test_failed_or_ambiguous_start_commit_never_simulates(self):
        for ambiguous in (False,True):
            with self.subTest(ambiguous=ambiguous):
                with self.connect(factory=AmbiguousConnection) as db:
                    fake = FakeTransport(Outcome.ACCEPTED)
                    db.fail_commit = True; db.commit_then_fail = ambiguous
                    with self.assertRaises(sqlite3.OperationalError):
                        simulate_attempt(db,self.lease,fake,max_attempts=3,retry_delay=WAIT,clock=lambda: NOW)
                    self.assertEqual(fake.calls,0)
                count = self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0]
                self.assertEqual(count, int(ambiguous))
        self.assertEqual(recover_expired(self.db,now=NOW+timedelta(minutes=2)).status,'unknown'); self.db.commit()

    def test_rejects_real_transport_and_invalid_policy(self):
        with self.assertRaises(TypeError): simulate_attempt(self.db,self.lease,object(),max_attempts=3,retry_delay=WAIT)
        with self.assertRaises(ValueError): self.start(limit=0)
        with self.assertRaises(ValueError): self.start(now=datetime(2026,1,1))
        self.assertFalse(self.db.in_transaction)

    def test_insert_failure_never_invokes_fake_and_rolls_back(self):
        self.db.execute("CREATE TRIGGER reject_attempt BEFORE INSERT ON email_delivery_attempts BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        self.db.commit()
        fake = FakeTransport(Outcome.ACCEPTED)
        with self.assertRaises(sqlite3.IntegrityError):
            simulate_attempt(self.db, self.lease, fake, max_attempts=3, retry_delay=WAIT, clock=lambda: NOW)
        self.assertEqual(fake.calls, 0)
        self.assertEqual(self.state()[1], 0)

    def test_crash_at_fake_leaves_started_for_recovery(self):
        fake = FakeTransport(Outcome.ACCEPTED)
        with patch.object(FakeTransport, 'simulate', side_effect=RuntimeError('synthetic crash')):
            with self.assertRaises(RuntimeError):
                simulate_attempt(self.db, self.lease, fake, max_attempts=3, retry_delay=WAIT, clock=lambda: NOW)
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'started')
        self.assertEqual(recover_expired(self.db, now=NOW+timedelta(minutes=2)).status, 'unknown')
        self.db.commit()

    def test_finish_failure_rolls_back_both_records(self):
        attempt = self.start(); self.db.commit()
        self.db.execute("CREATE TRIGGER reject_finish BEFORE UPDATE OF status ON email_outbox BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(attempt, Outcome.ACCEPTED)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0], 'started')
        self.assertEqual(self.state()[0], 'processing')

    def test_simulation_never_rolls_back_caller_transaction(self):
        self.db.execute("UPDATE email_outbox SET terminal_reason='caller_work'")
        fake = FakeTransport(Outcome.ACCEPTED)
        with self.assertRaises(ValueError):
            simulate_attempt(self.db, self.lease, fake, max_attempts=3, retry_delay=WAIT, clock=lambda: NOW)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT terminal_reason FROM email_outbox').fetchone()[0], 'caller_work')
        self.assertEqual(fake.calls, 0)
        self.db.rollback()

    def test_duplicate_finish_is_fenced(self):
        attempt = self.start(); self.db.commit()
        self.finish(attempt,Outcome.ACCEPTED); self.db.commit()
        with self.assertRaises(ValueError): self.finish(attempt,Outcome.ACCEPTED)
        self.db.rollback()
        self.assertEqual(self.state()[0], 'accepted')


if __name__ == '__main__':
    unittest.main()
