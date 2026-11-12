"""Eligibility checks against isolated migrated SQLite and synthetic records."""
from datetime import datetime, timedelta, timezone
import sqlite3
import unittest
from unittest.mock import patch

from app.email.attempt_state import Outcome, finish_attempt
from app.email.invitation_eligibility import (InvitationIneligible, check_invitation_eligibility,
                                               start_eligible_attempt)
from app.email.worker_store import Lease, claim_next
from tests import test_email_outbox_migration as fixture

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
STAMP = fixture.STAMP


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp(); self.addCleanup(self.fx.tearDown)
        self.fx.migrate()
        self.db = self.connect(); self.addCleanup(self.db.close)
        # The generic fixture creates current metadata; apply real 008 only to
        # this disposable database after removing its empty pre-created tables.
        self.db.execute('DROP TABLE email_verification_challenges')
        self.db.execute('DROP TABLE user_email_verifications')
        self.db.commit()
        fixture.load_migration('008_email_verification.py').migrate(self.fx.path)
        self.db.execute("UPDATE manuscripts SET review_round=2,status='revised' WHERE manuscript_id=1")
        self.db.execute('UPDATE review_assignments SET review_round=2 WHERE assignment_id=1')
        self.db.execute("INSERT INTO user_email_verifications(user_id,email,verified_at,created_at,updated_at) VALUES (2,'reviewer@example.com',?,?,?)", (STAMP,STAMP,STAMP))
        self.db.execute("INSERT INTO review_round_files(round_file_id,manuscript_id,review_round,source_kind,confirmation_id,file_path,created_at) VALUES (1,1,2,'author_revision',NULL,'synthetic.docx',?)", (STAMP,))
        self.db.execute('INSERT INTO review_assignment_files(assignment_id,round_file_id,bound_at) VALUES (1,1,?)',(STAMP,))
        self.fx.insert_event(self.db); self.fx.insert_outbox(self.db); self.db.commit()
        self.lease = claim_next(self.db, lease_duration=timedelta(minutes=2), now=NOW); self.db.commit()

    def connect(self):
        db = sqlite3.connect(self.fx.path, timeout=0.1)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def check(self, lease=None):
        return check_invitation_eligibility(self.db,lease or self.lease,now=NOW)

    def mutate(self, sql, args=()):
        self.db.execute(sql,args); self.db.commit()

    def reject(self):
        with self.assertRaises(InvitationIneligible): self.check()
        self.db.rollback()

    def test_valid_no_network_and_atomic_attempt_compatibility(self):
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            attempt, result = start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        self.assertEqual((result.assignment_id,result.recipient_user_id,result.review_round),(1,2,2))
        self.assertEqual(attempt.number,1)
        self.db.commit()
        self.assertEqual(finish_attempt(self.db,self.lease,attempt,Outcome.UNKNOWN,max_attempts=3,retry_delay=timedelta(seconds=5),now=NOW),'unknown')
        self.db.commit()
        self.assertIsNone(claim_next(self.db,lease_duration=timedelta(minutes=1),now=NOW+timedelta(days=1)))
        self.db.rollback()

    def test_first_round_confirmation_binding(self):
        self.db.execute("INSERT INTO initial_editing_submissions(submission_id,manuscript_id,editor_id,file_path,comments,status,created_at) VALUES (1,1,3,'synthetic.pdf','synthetic','eic_approved',?)",(STAMP,))
        self.db.execute("INSERT INTO pre_review_confirmations(confirmation_id,manuscript_id,submission_id,author_id,file_path,decision,comments,confirmed_at) VALUES (1,1,1,1,'synthetic.pdf','confirm','synthetic',?)",(STAMP,))
        self.db.execute("UPDATE manuscripts SET review_round=1,status='ready_for_peer_review' WHERE manuscript_id=1")
        self.db.execute('UPDATE review_assignments SET review_round=1 WHERE assignment_id=1')
        self.db.execute("UPDATE review_round_files SET review_round=1,source_kind='initial_editing',confirmation_id=1 WHERE round_file_id=1")
        self.db.commit()
        self.assertEqual(self.check().review_round,1)
        self.db.rollback()

    def test_cancelled_outbox_is_not_eligible(self):
        self.mutate("UPDATE email_outbox SET status='cancelled',lease_token=NULL,lease_until=NULL")
        self.reject()

    def test_legacy_competing_assignment_is_ambiguous(self):
        self.mutate("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (3,1,2,3,2,'assigned',?)",(STAMP,))
        self.reject()

    def test_reviewer_role_and_missing_reviewer(self):
        self.mutate("UPDATE users SET role='editor' WHERE user_id=2"); self.reject()
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.mutate('DELETE FROM users WHERE user_id=2')
        self.db.execute('PRAGMA foreign_keys=ON'); self.reject()

    def test_unverified_changed_and_invalid_email(self):
        for sql in ("UPDATE user_email_verifications SET verified_at=NULL WHERE user_id=2",
                    "UPDATE user_email_verifications SET email='old@example.com',verified_at='2026-01-02T03:04:05.000000Z' WHERE user_id=2",
                    "DELETE FROM user_email_verifications WHERE user_id=2"):
            with self.subTest(sql=sql):
                self.db.execute('SAVEPOINT scenario'); self.db.execute(sql)
                self.db.execute('RELEASE scenario'); self.db.commit()
                self.reject()
                self.db.execute('DELETE FROM user_email_verifications')
                self.db.execute("INSERT INTO user_email_verifications VALUES (2,'reviewer@example.com',?,?,?)",(STAMP,STAMP,STAMP)); self.db.commit()
        self.mutate("UPDATE users SET email='not-an-email' WHERE user_id=2")
        self.mutate("UPDATE user_email_verifications SET email='not-an-email' WHERE user_id=2"); self.reject()

    def test_declined_accepted_or_missing_invitation(self):
        for status in ('accepted','declined'):
            self.mutate('UPDATE reviewer_invitations SET status=? WHERE assignment_id=1',(status,)); self.reject()
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.mutate('DELETE FROM reviewer_invitations WHERE assignment_id=1')
        self.db.execute('PRAGMA foreign_keys=ON'); self.reject()

    def test_invalid_or_missing_assignment(self):
        self.mutate("UPDATE review_assignments SET status='completed' WHERE assignment_id=1"); self.reject()
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.mutate('DELETE FROM review_assignments WHERE assignment_id=1')
        self.db.execute('PRAGMA foreign_keys=ON'); self.reject()

    def test_round_and_manuscript_state_change(self):
        self.mutate('UPDATE manuscripts SET review_round=3 WHERE manuscript_id=1'); self.reject()
        self.mutate("UPDATE manuscripts SET review_round=2,status='accepted' WHERE manuscript_id=1"); self.reject()
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.mutate('DELETE FROM manuscripts WHERE manuscript_id=1')
        self.db.execute('PRAGMA foreign_keys=ON'); self.reject()

    def test_missing_or_wrong_file_binding(self):
        self.mutate('DELETE FROM review_assignment_files WHERE assignment_id=1'); self.reject()
        self.mutate('INSERT INTO review_assignment_files VALUES (1,1,?)',(STAMP,))
        self.mutate('UPDATE review_round_files SET review_round=3 WHERE round_file_id=1'); self.reject()

    def test_duplicate_active_assignment_is_ambiguous(self):
        self.mutate("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (3,1,2,3,2,'assigned',?)",(STAMP,))
        self.mutate("INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (3,'pending',?)",(STAMP,))
        self.reject()

    def test_inconsistent_event_template_or_recipient(self):
        self.mutate("UPDATE email_outbox SET template_version='v2'"); self.reject()
        self.mutate("UPDATE email_outbox SET template_version='v1',recipient_user_id=4"); self.reject()

    def test_missing_verification_schema_fails_closed_and_attempt_rolls_back(self):
        self.mutate('DROP TABLE user_email_verifications')
        with self.assertRaises(sqlite3.OperationalError):
            start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT attempt_count FROM email_outbox').fetchone()[0],0)

    def test_failed_validation_rolls_back_start(self):
        self.mutate("UPDATE reviewer_invitations SET status='declined'")
        with self.assertRaises(InvitationIneligible):
            start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT attempt_count FROM email_outbox').fetchone()[0],0)

    def test_preexisting_stale_observation_not_reused(self):
        self.assertEqual(self.check().assignment_id,1); self.db.commit()
        with self.connect() as other:
            other.execute("UPDATE reviewer_invitations SET status='declined' WHERE assignment_id=1"); other.commit()
        with self.assertRaises(InvitationIneligible):
            start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0],0)

    def test_write_lock_prevents_concurrent_change_during_check_and_start(self):
        attempt,_ = start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("UPDATE reviewer_invitations SET status='declined' WHERE assignment_id=1")
        self.db.commit()
        with self.connect() as other:
            other.execute("UPDATE reviewer_invitations SET status='declined' WHERE assignment_id=1"); other.commit()
        self.assertEqual(attempt.number,1)

    def test_wrong_or_expired_lease_and_unknown_status(self):
        with self.assertRaises(InvitationIneligible): self.check(Lease(self.lease.outbox_id,'wrong',self.lease.expires_at))
        self.db.rollback()
        with self.assertRaises(InvitationIneligible): check_invitation_eligibility(self.db,self.lease,now=NOW+timedelta(minutes=2))
        self.db.rollback()
        self.mutate("UPDATE email_outbox SET status='unknown',lease_token=NULL,lease_until=NULL")
        self.reject()

    def test_existing_transaction_is_not_rolled_back(self):
        self.db.execute("UPDATE manuscripts SET title='caller work'")
        with self.assertRaises(ValueError): start_eligible_attempt(self.db,self.lease,max_attempts=3,now=NOW)
        self.assertTrue(self.db.in_transaction); self.db.rollback()


if __name__ == '__main__': unittest.main()
