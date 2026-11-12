"""Existing lifecycle policy regressions on disposable SQLite data only."""
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'synthetic-lifecycle-test-key')

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.email.invitation_eligibility import InvitationIneligible, start_eligible_attempt
from app.email.verification_service import is_email_verified
from app.models import User
from app.routers import users
from app.schemas import UserLogin
from tests import test_invitation_email_eligibility as fixtures


class LifecyclePolicyTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EligibilityTest('test_valid_no_network_and_atomic_attempt_compatibility')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.engine = create_engine(f'sqlite:///{self.fixture.fx.path}')
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db_patches = [patch.object(users, 'IS_DEMO', False),
                           patch.object(users, 'limit_login'),
                           patch.object(users, 'verify_password', return_value=True),
                           patch.object(users, 'create_access_token', return_value='synthetic-token'),
                           patch.object(users, 'decode_access_token', return_value=2)]
        for item in self.db_patches:
            item.start(); self.addCleanup(item.stop)

    def mutate(self, sql, args=()):
        self.db.execute(sql,args); self.db.commit()

    def login(self, db):
        return users.login_user(UserLogin(email='reviewer@example.com',password='synthetic-password'), None, db)

    def authenticated(self, db):
        return users.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials='synthetic-token'),db)

    def rejected(self):
        with self.assertRaises(InvitationIneligible):
            start_eligible_attempt(self.db,self.fixture.lease,max_attempts=3,now=fixtures.NOW)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM email_delivery_attempts').fetchone()[0],0)

    def test_verified_login_and_current_user_preserved(self):
        with self.Session() as db:
            self.assertEqual(self.login(db)['token_type'], 'bearer')
            self.assertEqual(self.authenticated(db).user_id,2)
            self.assertTrue(is_email_verified(db,db.get(User,2)))

    def test_unverified_account_blocked_for_login_jwt_and_invitation(self):
        self.mutate('UPDATE user_email_verifications SET verified_at=NULL WHERE user_id=2')
        with self.Session() as db:
            for action in (self.login,self.authenticated):
                with self.assertRaises(HTTPException) as error: action(db)
                self.assertEqual(error.exception.status_code,403)
        self.rejected()

    def test_legacy_account_login_compatibility_does_not_imply_email_verification(self):
        self.mutate('DELETE FROM user_email_verifications WHERE user_id=2')
        with self.Session() as db:
            self.assertEqual(self.login(db)['token_type'],'bearer')
            self.assertEqual(self.authenticated(db).user_id,2)
            self.assertFalse(is_email_verified(db,db.get(User,2)))
        self.rejected()

    def test_changed_address_or_revoked_verification_invalidate_intent(self):
        self.mutate("UPDATE users SET email='new-reviewer@example.com' WHERE user_id=2")
        with self.Session() as db:
            self.assertFalse(is_email_verified(db,db.get(User,2)))
            with self.assertRaises(HTTPException) as error: self.authenticated(db)
            self.assertEqual(error.exception.status_code,403)
        self.rejected()

    def test_role_change_immediately_invalidates_assignment_intent(self):
        self.mutate("UPDATE users SET role='editor' WHERE user_id=2")
        with self.Session() as db:
            reviewer = self.authenticated(db)
            with self.assertRaises(HTTPException) as error: users.require_role('reviewer')(reviewer)
            self.assertEqual(error.exception.status_code,403)
        self.rejected()

    def test_decline_and_round_advance_invalidate_existing_intent(self):
        self.mutate("UPDATE reviewer_invitations SET status='declined' WHERE assignment_id=1")
        self.rejected()
        self.mutate("UPDATE reviewer_invitations SET status='pending' WHERE assignment_id=1")
        self.mutate('UPDATE manuscripts SET review_round=3 WHERE manuscript_id=1')
        self.rejected()

    def test_ambiguity_does_not_infer_explicit_cancellation(self):
        self.mutate("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (3,1,2,3,2,'assigned',?)",(fixtures.STAMP,))
        self.rejected()
        self.assertEqual(self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0], 'pending')
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox').fetchone()[0], 'processing')

    def test_new_round_and_expired_lease_are_not_authority(self):
        self.mutate('UPDATE manuscripts SET review_round=3 WHERE manuscript_id=1')
        self.rejected()
        with self.assertRaises(ValueError):
            start_eligible_attempt(self.db,self.fixture.lease,max_attempts=3,now=fixtures.NOW+timedelta(minutes=3))
        self.assertFalse(self.db.in_transaction)


if __name__ == '__main__': unittest.main()
