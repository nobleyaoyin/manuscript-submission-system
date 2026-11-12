"""Synthetic identities and disposable Migration 009 fixtures only."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import jwt

from app.lifecycle_prototype import (AuthenticatedPrincipal, PolicyError,
    SyntheticPrincipalAuthority, lifecycle_change, invalidate_invitation,
    validate_synthetic_jwt)
from tests.test_lifecycle_prototype import LifecyclePrototypeTest, KEY, AUTHORITY, principal


class HardeningTest(unittest.TestCase):
    def setUp(self):
        self.fx = LifecyclePrototypeTest('test_enrollment_disable_reactivate_history')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.db = self.fx.db

    def attempt(self, identity, authority=AUTHORITY):
        return lifecycle_change(self.db, identity, 2, 'enroll', authority=authority,
                                expected_sequence=0, expected_generation=0)

    def test_untrusted_ids_forged_objects_and_foreign_authority(self):
        foreign = SyntheticPrincipalAuthority('another-synthetic-key-at-least-32-bytes')
        token = jwt.encode({'sub':'3','gen':1,'exp':datetime.now(timezone.utc)+timedelta(minutes=5)},
                           'another-synthetic-key-at-least-32-bytes', algorithm='HS256')
        for identity in (3, {'user_id':3,'role':'eic'}, SimpleNamespace(user_id=3,role='eic'),
                         AuthenticatedPrincipal(), foreign.authenticate(token)):
            with self.subTest(identity=type(identity).__name__), self.assertRaises(PolicyError):
                self.attempt(identity)
        self.assertEqual(self.db.execute('SELECT count(*) FROM account_lifecycle_events WHERE user_id=2').fetchone()[0],0)

    def test_injected_role_is_not_a_claim_and_disabled_actor_rejected(self):
        p = principal(3)
        with self.assertRaises(AttributeError): p.role = 'eic'
        self.db.execute("UPDATE users SET role='editor' WHERE user_id=3"); self.db.commit()
        with self.assertRaises(PolicyError): self.attempt(p)

    def test_present_malformed_generation_is_not_legacy(self):
        for value in (None, True, '1', -1, 0, 1.0):
            token = self.fx.token(gen=value)
            with self.subTest(value=value):
                self.assertFalse(validate_synthetic_jwt(self.db,token,KEY,expected_user_id=2,allow_legacy=True))
        self.assertTrue(self.fx.check(self.fx.token(),legacy=True))

    def test_stale_principal_after_disable_and_reactivation(self):
        self.fx.change('enroll',0,0)
        self.db.execute("UPDATE users SET role='eic' WHERE user_id=2"); self.db.commit()
        p = principal(2)
        self.fx.change('disable',1,1)
        with self.assertRaises(PolicyError):
            invalidate_invitation(self.db,p,1,authority=AUTHORITY)
        self.fx.change('reactivate',2,2)
        with self.assertRaises(PolicyError):
            invalidate_invitation(self.db,p,1,authority=AUTHORITY)

    def test_verifier_rejects_forged_claim_types_and_signatures(self):
        for gen in (None, True, '1', -1, 0):
            token=jwt.encode({'sub':'3','gen':gen,'exp':datetime.now(timezone.utc)+timedelta(minutes=1)},KEY,algorithm='HS256')
            with self.subTest(gen=gen),self.assertRaises(PolicyError): AUTHORITY.authenticate(token)
        with self.assertRaises(PolicyError):
            AUTHORITY.authenticate(jwt.encode({'sub':'3','gen':1,'exp':datetime.now(timezone.utc)+timedelta(minutes=1)},'wrong-synthetic-key-at-least-32-bytes',algorithm='HS256'))


if __name__ == '__main__': unittest.main()
