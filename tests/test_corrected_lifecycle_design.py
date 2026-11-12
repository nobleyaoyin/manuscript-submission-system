"""Test-only corrected lifecycle design; all data is disposable and synthetic."""
import re
import sqlite3
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import jwt

from tests import test_lifecycle_design_validation as old
from tests import test_email_outbox_migration as fixtures

SQL = old.SQL.replace('sequence > 1 AND from_state =', 'sequence > 1 AND from_state IS NOT NULL AND from_state =')
SQL += '\nCREATE UNIQUE INDEX uq_invalidation_replacement ON reviewer_invitation_invalidations(replacement_assignment_id) WHERE replacement_assignment_id IS NOT NULL;\n'
DDL = list(old.statements(SQL))
OBJECTS = {re.search(r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)\s+(\w+)',s,re.I).group(1): s for s in DDL}
STAMP = fixtures.STAMP


def apply(db, **kw):
    with patch.object(old,'DDL',DDL), patch.object(old,'OBJECTS',OBJECTS):
        return old.apply_design(db,**kw)


def auth(db, user_id, generation):
    """Synthetic claim policy, not actual JWT authentication."""
    row=db.execute('SELECT state,auth_generation FROM account_lifecycle WHERE user_id=?',(user_id,)).fetchone()
    if row is None: return generation is None  # approved assumption: legacy login only
    return row[0]=='active' and type(generation) is int and generation==row[1]


def invalidate(db, target, kind, replacement=None):
    """Old test-only transaction implements the pending/same-round policies."""
    return old.invalidate(db,target,kind,replacement)


def response(db, assignment):
    """Synthetic conditional response; not the application endpoint."""
    db.execute('BEGIN IMMEDIATE')
    try:
        n=db.execute("""UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=?
            AND status='pending' AND NOT EXISTS
            (SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=?)""",(assignment,assignment)).rowcount
        if n != 1: raise ValueError('no longer pending')
        db.commit()
    except Exception:
        db.rollback(); raise


def admin_disable(db, actor, target):
    """Synthetic policy with all reads/writes under one SQLite write lock."""
    db.execute('BEGIN IMMEDIATE')
    try:
        a=db.execute("SELECT u.role,l.state FROM users u JOIN account_lifecycle l ON l.user_id=u.user_id WHERE u.user_id=?",(actor,)).fetchone()
        targetrow=db.execute('SELECT state,sequence,auth_generation FROM account_lifecycle WHERE user_id=?',(target,)).fetchone()
        role=db.execute('SELECT role FROM users WHERE user_id=?',(target,)).fetchone()
        if a != ('eic','active') or actor==target or not targetrow or targetrow[0]!='active': raise ValueError('forbidden')
        if role == ('eic',) and db.execute("SELECT count(*) FROM users u JOIN account_lifecycle l ON l.user_id=u.user_id WHERE u.role='eic' AND l.state='active'").fetchone()[0] <= 1: raise ValueError('last eic')
        seq,gen=targetrow[1]+1,targetrow[2]+1
        eid='admin-event-'+str(target)+'-'+str(seq)
        db.execute("INSERT INTO account_lifecycle_events VALUES (?,?,?,'active','disabled',?,?,?,'administrative_disable')",(eid,target,seq,gen,actor,STAMP))
        n=db.execute("UPDATE account_lifecycle SET state='disabled',sequence=?,auth_generation=?,last_event_id=? WHERE user_id=? AND state='active' AND sequence=? AND auth_generation=?",(seq,gen,eid,target,targetrow[1],targetrow[2])).rowcount
        if n != 1: raise ValueError('stale')
        db.commit()
    except Exception:
        db.rollback();raise


class CorrectedDesignTest(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp();self.addCleanup(self.fx.tearDown);self.fx.migrate()
        self.db=self.connect();self.addCleanup(self.db.close)
        self.db.execute('DROP TABLE email_verification_challenges');self.db.execute('DROP TABLE user_email_verifications');self.db.commit()
        fixtures.load_migration('008_email_verification.py').migrate(self.fx.path)

    def connect(self):
        db=sqlite3.connect(self.fx.path,timeout=.1);db.execute('PRAGMA foreign_keys=ON');return db

    def enroll(self):
        old.event(self.db,'event-1',1,None,'active',1,'initial_enrollment')
        self.db.execute("INSERT INTO account_lifecycle VALUES (2,'active',1,1,'event-1')");self.db.commit()

    def replacement(self, aid=3, mid=1, rnd=1, reviewer=4, status='pending'):
        self.db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (?,?,?,?,?,'assigned',?)",(aid,mid,reviewer,3,rnd,STAMP))
        self.db.execute('INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (?,?,?)',(aid,status,STAMP));self.db.commit()

    def test_apply_twice_empty_and_preserve_contents(self):
        before=fixtures.EmailOutboxMigrationTest.contents(self.db);apply(self.db);apply(self.db)
        after=fixtures.EmailOutboxMigrationTest.contents(self.db)
        self.assertEqual(before,{k:after[k] for k in before})
        self.assertTrue(all(not after[k] for k in old.TABLES))
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_reject_null_noninitial_transition(self):
        apply(self.db);self.enroll()
        with self.assertRaises(sqlite3.IntegrityError): old.event(self.db,'bad',2,None,'disabled',2,'administrative_disable')
        self.db.rollback()

    def test_sql_cannot_guarantee_gap_or_generation(self):
        apply(self.db);self.enroll()
        old.event(self.db,'gap',5,'active','disabled',1,'administrative_disable');self.db.commit()
        self.assertEqual(self.db.execute("SELECT sequence,auth_generation FROM account_lifecycle_events WHERE event_id='gap'").fetchone(),(5,1))

    def test_valid_transitions_and_cas_reject_stale(self):
        apply(self.db);self.enroll();old.transition(self.db,1,1,'disabled');old.transition(self.db,2,2,'active')
        with self.assertRaises(ValueError):old.transition(self.db,1,1,'disabled')
        with self.assertRaises(ValueError):old.transition(self.db,3,3,'active')
        self.assertEqual(self.db.execute('SELECT state,sequence,auth_generation FROM account_lifecycle').fetchone(),('active',3,3))

    def test_synthetic_signed_jwt_generation_and_legacy(self):
        apply(self.db)
        key='synthetic-test-only-key-32-characters-long'
        def check(claims):
            token=jwt.encode(claims,key,algorithm='HS256')
            decoded=jwt.decode(token,key,algorithms=['HS256'],options={'verify_exp':False})
            return auth(self.db,2,decoded.get('gen'))
        self.assertTrue(check({'sub':'2'}));self.assertFalse(check({'sub':'2','gen':1}))
        self.enroll();self.assertFalse(check({'sub':'2'}));self.assertTrue(check({'sub':'2','gen':1}))
        old.transition(self.db,1,1,'disabled');self.assertFalse(check({'sub':'2','gen':1}))
        old.transition(self.db,2,2,'active');self.assertFalse(check({'sub':'2','gen':1}));self.assertTrue(check({'sub':'2','gen':3}))

    def test_missing_row_not_email_authority(self):
        apply(self.db)
        self.assertIsNone(self.db.execute('SELECT state FROM account_lifecycle WHERE user_id=2').fetchone())
        self.assertTrue(auth(self.db,2,None))
        # Email policy requires explicit active, independent of legacy login.
        self.assertFalse(bool(self.db.execute("SELECT 1 FROM account_lifecycle WHERE user_id=2 AND state='active'").fetchone()))

    def test_concurrent_lifecycle_requests_and_stale_generation(self):
        apply(self.db);self.enroll();self.db.execute('BEGIN IMMEDIATE')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError):old.transition(other,1,1,'disabled')
        self.db.rollback();old.transition(self.db,1,1,'disabled')
        with self.connect() as other:
            with self.assertRaises(ValueError):old.transition(other,1,1,'disabled')

    def test_composite_fk_audit_and_bad_actor(self):
        apply(self.db);self.enroll()
        for sql in ("UPDATE account_lifecycle SET state='disabled'", "DELETE FROM account_lifecycle_events", "UPDATE account_lifecycle_events SET actor_user_id=1"):
            with self.assertRaises(sqlite3.IntegrityError):self.db.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute("INSERT INTO account_lifecycle_events VALUES ('no-user',999,1,NULL,'active',1,3,?,'initial_enrollment')",(STAMP,))
        self.db.rollback()

    def test_same_replacement_unique_even_different_sources(self):
        apply(self.db);self.replacement();self.replacement(aid=4,reviewer=2)
        invalidate(self.db,1,'superseded',3)
        with self.assertRaises(sqlite3.IntegrityError):invalidate(self.db,4,'superseded',3)

    def test_pending_supersession_cross_round_manuscript_reviewer(self):
        apply(self.db)
        self.db.execute("INSERT INTO manuscripts(manuscript_id,title,abstract,author_id,status,review_round,created_at) VALUES (2,'Synthetic','Synthetic',1,'revised',1,?)",(STAMP,));self.db.commit()
        for aid,mid,rnd,reviewer in ((3,2,1,4),(4,1,2,4),(5,1,1,2)):
            self.replacement(aid,mid,rnd,reviewer)
            with self.assertRaises(ValueError):invalidate(self.db,1,'superseded',aid)
        self.replacement(aid=6);invalidate(self.db,1,'superseded',6)

    def test_pending_only_and_duplicate(self):
        apply(self.db)
        for state in ('accepted','declined'):
            self.db.execute('UPDATE reviewer_invitations SET status=? WHERE assignment_id=1',(state,));self.db.commit()
            with self.assertRaises(ValueError):invalidate(self.db,1,'cancelled')
        self.db.execute("UPDATE reviewer_invitations SET status='pending' WHERE assignment_id=1");self.db.commit()
        invalidate(self.db,1,'cancelled')
        with self.assertRaises(sqlite3.IntegrityError):invalidate(self.db,1,'cancelled')
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('DELETE FROM reviewer_invitation_invalidations')
        self.db.rollback()

    def test_synthetic_cancellation_response_serialization_both_orders(self):
        apply(self.db);self.db.execute('BEGIN IMMEDIATE')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError):response(other,1)
        self.db.rollback();invalidate(self.db,1,'cancelled')
        with self.assertRaises(ValueError):response(self.db,1)
        self.replacement();response(self.db,3)
        with self.assertRaises(ValueError):invalidate(self.db,3,'cancelled')

    def test_admin_authority_and_self_disable(self):
        apply(self.db);self.enroll()
        # Explicitly seeded synthetic admin; this is not a bootstrap policy.
        self.db.execute("UPDATE users SET role='eic' WHERE user_id=3")
        self.db.execute("INSERT INTO account_lifecycle_events VALUES ('admin',3,1,NULL,'active',1,3,?,'initial_enrollment')",(STAMP,))
        self.db.execute("INSERT INTO account_lifecycle VALUES (3,'active',1,1,'admin')");self.db.commit()
        with self.assertRaises(ValueError):admin_disable(self.db,2,3)
        with self.assertRaises(ValueError):admin_disable(self.db,3,3)
        admin_disable(self.db,3,2)
        self.assertEqual(self.db.execute('SELECT state FROM account_lifecycle WHERE user_id=2').fetchone()[0],'disabled')

    def test_competing_cancellation_and_response(self):
        apply(self.db); barrier=threading.Barrier(2)
        def operation(kind):
            with self.connect() as db:
                barrier.wait(timeout=5)
                try:
                    (invalidate(db,1,'cancelled') if kind=='cancel' else response(db,1))
                    return 'success'
                except (ValueError,sqlite3.OperationalError):
                    return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(operation,('cancel','response')))
        self.assertEqual(result.count('success'),1)
        status=self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0]
        invalid=self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0]
        self.assertIn((status,invalid),[('pending',1),('accepted',0)])

    def test_direct_sql_still_bypasses_cross_row_policy(self):
        apply(self.db);self.replacement(rnd=2)
        self.db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('bad',1,'superseded',3,3,?,'replacement_selected')",(STAMP,));self.db.commit()
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviewer_invitation_invalidations').fetchone()[0],1)

    def test_started_and_unknown_are_not_retried(self):
        apply(self.db);self.fx.insert_event(self.db);self.fx.insert_outbox(self.db,status='processing',due=None,token='synthetic-lease',lease=STAMP);self.fx.insert_attempt(self.db);self.db.commit()
        invalidate(self.db,1,'cancelled')
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0],'started')
        self.db.execute("UPDATE email_delivery_attempts SET outcome='unknown',finished_at=?",(STAMP,));self.db.commit()
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0],'unknown')

    def test_missing_prerequisite_partial_failure_and_mismatch(self):
        self.db.execute("DELETE FROM schema_migrations WHERE migration_id='008_email_verification'");self.db.commit()
        with self.assertRaises(ValueError):apply(self.db)
        self.db.execute("INSERT INTO schema_migrations VALUES ('008_email_verification',?)",(STAMP,));self.db.commit()
        self.db.execute('CREATE VIEW account_lifecycle AS SELECT 1');self.db.commit()
        with self.assertRaises(ValueError):apply(self.db)
        self.db.execute('DROP VIEW account_lifecycle');self.db.commit()
        with self.assertRaises(RuntimeError):apply(self.db,fail_at=2)
        self.assertFalse(set(OBJECTS)&{x[0] for x in self.db.execute('SELECT name FROM sqlite_master')})
        apply(self.db);self.db.execute('DROP INDEX uq_invalidation_replacement');self.db.commit()
        with self.assertRaises(ValueError):apply(self.db)


if __name__ == '__main__':unittest.main()
