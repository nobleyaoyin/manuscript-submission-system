"""Adversarial review tests: only temporary databases and in-process requests."""
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace
import sqlite3
import os
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.security import HTTPAuthorizationCredentials
with patch.dict(os.environ, {'SECRET_KEY': 'synthetic-only-key-material-for-test', 'MANUSCRIPT_MODE': 'development'}), patch('dotenv.load_dotenv', return_value=False):
    from app.routers import users
import jwt

from app.lifecycle_prototype import lifecycle_change, validate_synthetic_jwt, PolicyError
from app.email.invitation_eligibility import start_eligible_attempt
from app.schemas import UserLogin
from tests import test_lifecycle_prototype as proto_fixture
from tests import test_invitation_email_eligibility as eligibility_fixture
from tests import test_reviewer_invitation_email_integration as route_fixture

m7=import_module('migrations.007_email_outbox')
m8=import_module('migrations.008_email_verification')
m9=import_module('migrations.009_account_invitation_lifecycle')
STAMP='2026-01-02T03:04:05.000000Z'


class CommitThenFail(sqlite3.Connection):
    def commit(self):
        super().commit()
        raise sqlite3.OperationalError('synthetic lost commit acknowledgement')


class PrototypeSecurityReview(unittest.TestCase):
    def setUp(self):
        self.fx=proto_fixture.LifecyclePrototypeTest('test_enrollment_disable_reactivate_history')
        self.fx.setUp();self.addCleanup(self.fx.doCleanups)

    def test_untrusted_actor_id_is_not_authenticated_by_prototype(self):
        with self.assertRaises(PolicyError):
            lifecycle_change(self.fx.db,3,2,'enroll',authority=proto_fixture.AUTHORITY,expected_sequence=0,expected_generation=0)
        self.assertIsNone(self.fx.db.execute('SELECT 1 FROM account_lifecycle_events WHERE user_id=2').fetchone())

    def test_null_generation_is_rejected_when_legacy_allowed(self):
        # Distinguishes an absent claim from an explicitly malformed null claim.
        payload={'sub':'2','exp':datetime.now(timezone.utc)+timedelta(minutes=1),'gen':None}
        token=jwt.encode(payload,proto_fixture.KEY,algorithm='HS256')
        self.assertFalse(validate_synthetic_jwt(self.fx.db,token,proto_fixture.KEY,expected_user_id=2,allow_legacy=True))

    def test_current_authentication_ignores_prototype_disabled_state(self):
        self.fx.change('enroll',0,0)
        self.fx.change('disable',1,1)
        engine=create_engine(f'sqlite:///{self.fx.fx.fx.path}')
        try:
            with sessionmaker(bind=engine)() as db, patch.object(users,'IS_DEMO',True), patch.object(users,'decode_access_token',return_value=2):
                account=users.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer',credentials='synthetic'),db)
                self.assertEqual(account.user_id,2)
        finally:engine.dispose()

    def test_synthetic_demo_login_bypasses_unverified_status(self):
        self.fx.db.execute("INSERT INTO user_email_verifications(user_id,email,verified_at,created_at,updated_at) VALUES (2,'reviewer@example.com',NULL,?,?)",(STAMP,STAMP));self.fx.db.commit()
        engine=create_engine(f'sqlite:///{self.fx.fx.fx.path}')
        try:
            with sessionmaker(bind=engine)() as db, patch.object(users,'IS_DEMO',True), patch.object(users,'limit_login'), patch.object(users,'verify_password',return_value=True), patch.object(users,'create_access_token',return_value='synthetic'):
                result=users.login_user(UserLogin(email='reviewer@example.com',password='synthetic-password'),None,db)
                self.assertEqual(result['token_type'],'bearer')
        finally:engine.dispose()

    def test_commit_ack_failure_may_still_have_committed(self):
        path=self.fx.fx.fx.path
        db=sqlite3.connect(path,timeout=.2,factory=CommitThenFail)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with self.assertRaises(sqlite3.OperationalError):
                self.fx.change('enroll',0,0,db=db)
            self.assertFalse(db.in_transaction)
        finally:db.close()
        self.assertEqual(self.fx.db.execute('SELECT state FROM account_lifecycle WHERE user_id=2').fetchone()[0],'active')
        self.assertEqual(self.fx.db.execute('SELECT count(*) FROM account_lifecycle_events WHERE user_id=2').fetchone()[0],1)


class EligibilitySecurityReview(unittest.TestCase):
    def setUp(self):
        self.fx=eligibility_fixture.EligibilityTest('test_valid_no_network_and_atomic_attempt_compatibility')
        self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        m9.migrate(self.fx.fx.path)

    def test_cancelled_invitation_and_missing_lifecycle_are_not_checked_by_email_helper(self):
        self.fx.db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('synthetic',1,'cancelled',NULL,3,?,'editorial_cancellation')",(STAMP,));self.fx.db.commit()
        attempt,info=start_eligible_attempt(self.fx.db,self.fx.lease,max_attempts=3,now=eligibility_fixture.NOW)
        self.assertEqual((attempt.number,info.assignment_id),(1,1))
        self.fx.db.rollback()
        self.assertIsNone(self.fx.db.execute('SELECT 1 FROM account_lifecycle WHERE user_id=2').fetchone())


class RouteSecurityReview(unittest.TestCase):
    def setUp(self):
        self.fx=route_fixture.InvitationEmailIntegrationTest('test_default_disabled_and_untrusted_request_input_on_001_006')
        self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        m7.migrate(self.fx.path)
        with sqlite3.connect(self.fx.path) as db:
            db.execute('DROP TABLE email_verification_challenges');db.execute('DROP TABLE user_email_verifications');db.commit()
        m8.migrate(self.fx.path);m9.migrate(self.fx.path)

    def test_real_routes_ignore_invalidation_when_artificially_installed(self):
        invited=self.fx.invite();self.assertEqual(invited.status_code,201)
        aid=invited.json()['assignment_id']
        with sqlite3.connect(self.fx.path) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('synthetic',?,'cancelled',NULL,?,?,'editorial_cancellation')",(aid,self.fx.ids['Eic'],STAMP));db.commit()
        self.fx.actor=SimpleNamespace(user_id=self.fx.ids['Reviewer'],role='reviewer',name='Reviewer')
        response=self.fx.client.post(f'/assignments/{aid}/invitation-response',json={'decision':'accept'})
        self.assertEqual(response.status_code,200)
        download=self.fx.client.get(f'/assignments/{aid}/download')
        self.assertEqual(download.status_code,200)
        review=self.fx.client.post('/reviews/',json={'assignment_id':aid,'comments':'Synthetic review','recommendation':'accept'})
        self.assertEqual(review.status_code,201)
        self.assertEqual(self.fx.client.get(f'/manuscripts/{self.fx.mid}/download').status_code,403)
        self.assertEqual(self.fx.client.get(f'/manuscripts/{self.fx.mid}/versions').status_code,403)


if __name__=='__main__':unittest.main()
