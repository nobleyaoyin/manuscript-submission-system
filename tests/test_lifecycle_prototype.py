"""Disconnected prototype tests with migrations 001–009 on temporary data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
import unittest
from unittest.mock import patch
import jwt

from app.lifecycle_prototype import (Lifecycle, PolicyError, SyntheticPrincipalAuthority,
    invalidate_invitation as _invalidate, lifecycle_change as _change,
    lifecycle_read as _read, validate_synthetic_jwt)
from tests import test_lifecycle_migration_009 as fixture

STAMP='2026-01-02T03:04:05.000000Z'
KEY='synthetic-only-signing-material-32-bytes'
AUTHORITY = SyntheticPrincipalAuthority(KEY)


def principal(actor):
    token = jwt.encode({'sub': str(actor), 'gen': 1,
                        'exp': datetime.now(timezone.utc) + timedelta(minutes=5)}, KEY, algorithm='HS256')
    return AUTHORITY.authenticate(token)


def lifecycle_change(db, actor, *args, **kwargs):
    return _change(db, principal(actor), *args, authority=AUTHORITY, **kwargs)


def lifecycle_read(db, actor, *args, **kwargs):
    return _read(db, principal(actor), *args, authority=AUTHORITY, **kwargs)


def invalidate_invitation(db, actor, *args, **kwargs):
    return _invalidate(db, principal(actor), *args, authority=AUTHORITY, **kwargs)


class LifecyclePrototypeTest(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.LifecycleMigrationTest('test_additive_idempotent_and_preserves_every_old_table')
        self.fx.setUp();self.addCleanup(self.fx.doCleanups);self.fx.migrate()
        self.db=self.connect();self.addCleanup(self.db.close)
        self.db.execute("UPDATE users SET role='eic' WHERE user_id=3")
        # Synthetic fixture only; this deliberately does not define bootstrap.
        self.seed_active(3)
        self.db.commit()
        guard=patch('socket.socket',side_effect=AssertionError('network forbidden'))
        guard.start();self.addCleanup(guard.stop)

    def connect(self):
        db=sqlite3.connect(self.fx.fx.path,timeout=.2);db.execute('PRAGMA foreign_keys=ON');return db

    def seed_active(self, uid):
        eid='seed-'+str(uid)
        self.db.execute('INSERT INTO account_lifecycle_events VALUES (?,?,1,NULL,\'active\',1,?,?,\'initial_enrollment\')',(eid,uid,3,STAMP))
        self.db.execute("INSERT INTO account_lifecycle VALUES (?,'active',1,1,?)",(uid,eid))

    def change(self, action, seq, gen, target=2, actor=3, db=None):
        return lifecycle_change(db or self.db,actor,target,action,expected_sequence=seq,expected_generation=gen)

    def token(self, sub=2, gen='missing', exp=None, key=KEY):
        claims={'sub':str(sub),'exp':exp or datetime.now(timezone.utc)+timedelta(minutes=5)}
        if gen != 'missing':claims['gen']=gen
        return jwt.encode(claims,key,algorithm='HS256')

    def check(self, token, expected=2, legacy=False):
        return validate_synthetic_jwt(self.db,token,KEY,expected_user_id=expected,allow_legacy=legacy)

    def replacement(self, aid=3, mid=1, rnd=1, reviewer=4, state='pending'):
        self.db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (?,?,?,?,?,'assigned',?)",(aid,mid,reviewer,3,rnd,STAMP))
        self.db.execute('INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (?,?,?)',(aid,state,STAMP));self.db.commit()

    def test_enrollment_disable_reactivate_history(self):
        self.assertIsNone(lifecycle_read(self.db,3,2))
        self.assertEqual(self.change('enroll',0,0),Lifecycle('active',1,1))
        self.assertEqual(self.change('disable',1,1),Lifecycle('disabled',2,2))
        self.assertEqual(self.change('reactivate',2,2),Lifecycle('active',3,3))
        self.assertEqual(len(lifecycle_read(self.db,3,2,history=True)),3)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_invalid_actor_self_missing_disabled_and_stale(self):
        with self.assertRaises(PolicyError):self.change('enroll',0,0,actor=2)
        with self.assertRaises(PolicyError):self.change('disable',1,1,target=3)
        self.change('enroll',0,0)
        with self.assertRaises(PolicyError):self.change('enroll',0,0)
        with self.assertRaises(PolicyError):self.change('disable',1,2)
        with self.assertRaises(PolicyError):self.change('reactivate',1,1)
        self.db.execute("UPDATE users SET role='editor' WHERE user_id=3");self.db.commit()
        with self.assertRaises(PolicyError):self.change('disable',1,1)

    def test_disabled_admin_cannot_act(self):
        self.change('enroll',0,0)
        self.db.execute("UPDATE users SET role='eic' WHERE user_id=2");self.db.commit()
        self.change('disable',1,1)
        with self.assertRaises(PolicyError):self.change('enroll',0,0,target=4,actor=2)

    def test_jwt_signature_expiry_subject_types_and_reactivation(self):
        legacy=self.token();self.assertTrue(self.check(legacy,legacy=True));self.assertFalse(self.check(legacy))
        self.change('enroll',0,0);first=self.token(gen=1)
        self.assertTrue(self.check(first));self.assertFalse(self.check(legacy,legacy=True))
        for bad in (True,0,'1',1.0):self.assertFalse(self.check(self.token(gen=bad)))
        self.assertFalse(self.check(self.token(sub=4,gen=1)))
        self.assertFalse(self.check(self.token(gen=1,key='wrong-synthetic-key-of-length-32!!')))
        self.assertFalse(self.check(self.token(gen=1,exp=datetime.now(timezone.utc)-timedelta(seconds=10))))
        self.change('disable',1,1);self.assertFalse(self.check(first))
        self.change('reactivate',2,2);self.assertFalse(self.check(first));self.assertTrue(self.check(self.token(gen=3)))

    def test_jwt_missing_user_never_legacy(self):
        self.assertFalse(self.check(self.token(sub=999),expected=999,legacy=True))

    def test_concurrent_disable_only_one_succeeds(self):
        self.change('enroll',0,0);barrier=threading.Barrier(2)
        def worker(_):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:self.change('disable',1,1,db=db);return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(worker,range(2)))
        self.assertEqual(results.count('ok'),1)
        self.assertEqual(len(lifecycle_read(self.db,3,2,history=True)),2)

    def test_disable_reactivation_stale_and_retry(self):
        self.change('enroll',0,0);self.change('disable',1,1)
        with self.assertRaises(PolicyError):self.change('reactivate',1,1)
        self.assertEqual(self.change('reactivate',2,2),Lifecycle('active',3,3))
        with self.assertRaises(PolicyError):self.change('disable',1,1)

    def test_corrupted_audit_fails_closed(self):
        self.change('enroll',0,0)
        self.db.execute("INSERT INTO account_lifecycle_events VALUES ('gap',2,5,'active','disabled',5,3,?,'administrative_disable')",(STAMP,));self.db.commit()
        with self.assertRaises(PolicyError):self.change('disable',1,1)
        self.assertFalse(self.check(self.token(gen=1)))

    def test_transaction_failure_rolls_back_both_event_and_pointer(self):
        self.change('enroll',0,0)
        self.db.execute("CREATE TRIGGER synthetic_failure BEFORE UPDATE ON account_lifecycle BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.change('disable',1,1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM account_lifecycle_events WHERE user_id=2').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT state FROM account_lifecycle WHERE user_id=2').fetchone()[0],'active')

    def test_cancellation_authorization_and_duplicate(self):
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,2,1)
        invalidate_invitation(self.db,3,1)
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1)
        self.assertEqual(self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0],'pending')
        self.assertEqual(self.db.execute('SELECT count(*) FROM review_assignments WHERE assignment_id=1').fetchone()[0],1)

    def test_supersession_and_chain_rejected(self):
        self.replacement();invalidate_invitation(self.db,3,1,replacement_id=3,reason='replacement_selected')
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1,replacement_id=3,reason='replacement_selected')
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,3)
        self.assertEqual(self.db.execute('SELECT kind,replacement_assignment_id FROM reviewer_invitation_invalidations').fetchone(),('superseded',3))

    def test_invalid_replacement_and_nonpending_and_round(self):
        self.replacement(rnd=2)
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1,replacement_id=3,reason='replacement_selected')
        self.db.execute("UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=1");self.db.commit()
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1)
        self.db.execute("UPDATE reviewer_invitations SET status='declined' WHERE assignment_id=1");self.db.commit()
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1)
        self.db.execute("UPDATE reviewer_invitations SET status='pending' WHERE assignment_id=1");self.db.execute('UPDATE manuscripts SET review_round=2 WHERE manuscript_id=1');self.db.commit()
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,3,1)

    def test_concurrent_cancellation_and_response(self):
        barrier=threading.Barrier(2)
        def work(kind):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:
                    if kind=='cancel': invalidate_invitation(db,3,1)
                    else:
                        db.execute('BEGIN IMMEDIATE')
                        count=db.execute("UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=1 AND status='pending' AND NOT EXISTS (SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=1)").rowcount
                        if count!=1:db.rollback();return 'rejected'
                        db.commit()
                    return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,('cancel','response')))
        self.assertEqual(results.count('ok'),1)

    def test_competing_cancellations(self):
        barrier=threading.Barrier(2)
        def work(_):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:invalidate_invitation(db,3,1);return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,range(2)))
        self.assertEqual(results.count('ok'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0],1)

    def test_active_assigned_editor_can_invalidate(self):
        self.db.execute("INSERT INTO users(user_id,name,email,password_hash,role,created_at) VALUES (5,'Synthetic','editor5@example.com','x','editor',?)",(STAMP,))
        self.seed_active(5)
        self.db.execute("INSERT INTO initial_editor_invitations(manuscript_id,editor_id,invited_by,status,invited_at) VALUES (1,5,3,'accepted',?)",(STAMP,));self.db.commit()
        invalidate_invitation(self.db,5,1)
        self.assertEqual(self.db.execute('SELECT actor_user_id FROM reviewer_invitation_invalidations').fetchone()[0],5)

    def test_unassigned_or_missing_editor_lifecycle_rejected(self):
        self.db.execute("INSERT INTO users(user_id,name,email,password_hash,role,created_at) VALUES (5,'Synthetic','editor5@example.com','x','editor',?)",(STAMP,));self.db.commit()
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,5,1)
        self.seed_active(5);self.db.commit()
        with self.assertRaises(PolicyError):invalidate_invitation(self.db,5,1)

    def test_invalid_cross_manuscript_same_reviewer_and_missing_replacement(self):
        self.db.execute("INSERT INTO manuscripts(manuscript_id,title,abstract,author_id,status,review_round,created_at) VALUES (2,'Synthetic','Synthetic',1,'revised',1,?)",(STAMP,));self.db.commit()
        self.replacement(aid=3,mid=2);self.replacement(aid=4,reviewer=2)
        for rid in (3,4,999):
            with self.subTest(replacement=rid),self.assertRaises(PolicyError):
                invalidate_invitation(self.db,3,1,replacement_id=rid,reason='replacement_selected')

    def test_competing_supersession_and_response(self):
        self.replacement();barrier=threading.Barrier(2)
        def work(kind):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:
                    if kind=='supersede':invalidate_invitation(db,3,1,replacement_id=3,reason='replacement_selected')
                    else:
                        db.execute('BEGIN IMMEDIATE')
                        n=db.execute("UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=1 AND status='pending' AND NOT EXISTS (SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=1)").rowcount
                        if n != 1:db.rollback();return 'rejected'
                        db.commit()
                    return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,('supersede','response')))
        self.assertEqual(results.count('ok'),1)
        state=self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0]
        invalid=self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations WHERE assignment_id=1').fetchone()[0]
        self.assertIn((state,invalid),[('pending',1),('accepted',0)])

    def test_round_advance_lock_and_retry_rejects_stale_cancellation(self):
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('UPDATE manuscripts SET review_round=2 WHERE manuscript_id=1')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError):invalidate_invitation(other,3,1)
        self.db.commit()
        with self.connect() as other:
            with self.assertRaises(PolicyError):invalidate_invitation(other,3,1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0],0)

    def test_disable_two_eics_keeps_one_and_invalidates_old_token(self):
        self.change('enroll',0,0);self.db.execute("UPDATE users SET role='eic' WHERE user_id=2");self.db.commit()
        original=self.token(gen=1);self.assertTrue(self.check(original))
        self.change('disable',1,1);self.assertFalse(self.check(original))
        with self.assertRaises(PolicyError):self.change('disable',1,1,target=3,actor=2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM users u JOIN account_lifecycle l ON u.user_id=l.user_id WHERE u.role='eic' AND l.state='active'").fetchone()[0],1)

    def test_concurrent_enrollment_only_one_wins(self):
        barrier=threading.Barrier(2)
        def work(_):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:self.change('enroll',0,0,db=db);return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,range(2)))
        self.assertEqual(results.count('ok'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM account_lifecycle_events WHERE user_id=2').fetchone()[0],1)

    def test_competing_disable_and_stale_reactivation(self):
        self.change('enroll',0,0);barrier=threading.Barrier(2)
        def work(action):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:self.change(action,1,1,db=db);return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,('disable','reactivate')))
        self.assertEqual(results,['ok','rejected'])
        self.assertEqual(self.change('reactivate',2,2),Lifecycle('active',3,3))

    def test_duplicate_supersession_concurrency(self):
        self.replacement();barrier=threading.Barrier(2)
        def work(_):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:invalidate_invitation(db,3,1,replacement_id=3,reason='replacement_selected');return 'ok'
                except (PolicyError,sqlite3.OperationalError):return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,range(2)))
        self.assertEqual(results.count('ok'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0],1)

    def test_token_validation_during_lifecycle_lock_never_grants(self):
        self.change('enroll',0,0);token=self.token(gen=1)
        self.db.execute('BEGIN IMMEDIATE')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError):
                validate_synthetic_jwt(other,token,KEY,expected_user_id=2)
        self.db.rollback();self.change('disable',1,1)
        with self.connect() as other:
            self.assertFalse(validate_synthetic_jwt(other,token,KEY,expected_user_id=2))

    def test_invalidation_write_failure_rolls_back(self):
        self.db.execute("CREATE TRIGGER synthetic_cancel_failure BEFORE INSERT ON reviewer_invitation_invalidations BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):invalidate_invitation(self.db,3,1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0],'pending')

    def test_started_attempt_remains_unchanged(self):
        self.fx.fx.insert_event(self.db);self.fx.fx.insert_outbox(self.db,status='processing',due=None,token='fake-lease',lease=STAMP);self.fx.fx.insert_attempt(self.db);self.db.commit()
        invalidate_invitation(self.db,3,1)
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0],'started')


if __name__=='__main__':unittest.main()
