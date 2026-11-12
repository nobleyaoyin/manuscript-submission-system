"""Design experiment only: temp 001–008 SQLite; never a migration module."""
import re
import sqlite3
import unittest
from pathlib import Path

from tests import test_email_outbox_migration as fixture

REPORT = Path(__file__).resolve().parents[1] / 'reports/step_128_27_11_lifecycle_schema_design.md'
SQL = REPORT.read_text().split('```sql\n', 1)[1].split('\n```', 1)[0]

def statements(sql):
    buf = ''
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ''
    if buf.strip(): raise ValueError('Incomplete DDL')

DDL = list(statements(SQL))
OBJECTS = {re.search(r'CREATE\s+(?:TABLE|INDEX|TRIGGER)\s+(\w+)', s, re.I).group(1): s for s in DDL}
TABLES = ('account_lifecycle_events','account_lifecycle','reviewer_invitation_invalidations')
DESIGN_ID = 'design_validation_only_009'
STAMP = fixture.STAMP


def normalize(s):
    return ' '.join(s.strip().rstrip(';').lower().split())


def apply_design(db, *, fail_at=None):
    """Test harness, not a migration: caller supplies disposable connection."""
    db.execute('BEGIN IMMEDIATE')
    try:
        ledger = {r[0] for r in db.execute('SELECT migration_id FROM schema_migrations')}
        expected = {m.MIGRATION_ID if hasattr(m,'MIGRATION_ID') else m.ID for m in fixture.MIGRATIONS}
        expected.add('008_email_verification')
        if not expected <= ledger: raise ValueError('missing prerequisites')
        objects = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger','view')"))
        applied = DESIGN_ID in ledger
        if not applied:
            if any(name in objects for name in OBJECTS): raise ValueError('partial schema')
            for i, stmt in enumerate(DDL):
                db.execute(stmt)
                if fail_at == i: raise RuntimeError('injected failure')
            db.execute('INSERT INTO schema_migrations VALUES (?,?)',(DESIGN_ID, STAMP))
        objects = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger','view')"))
        if any(name not in objects or normalize(objects[name]) != normalize(ddl) for name,ddl in OBJECTS.items()):
            raise ValueError('schema mismatch')
        if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('foreign key violation')
        db.commit()
    except Exception:
        db.rollback(); raise


def event(db, eid, seq, before, after, generation, reason):
    db.execute('INSERT INTO account_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?)',
               (eid,2,seq,before,after,generation,3,STAMP,reason))


def transition(db, expected_seq, expected_generation, target):
    """Synthetic service algorithm; not application runtime."""
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT state,sequence,auth_generation FROM account_lifecycle WHERE user_id=2').fetchone()
        if row is None or row[1:] != (expected_seq,expected_generation): raise ValueError('stale')
        before = row[0]
        if (before,target) not in (('active','disabled'),('disabled','active')): raise ValueError('invalid transition')
        seq, gen = expected_seq+1, expected_generation+1
        eid = 'event-'+str(seq)
        event(db,eid,seq,before,target,gen,'administrative_disable' if target=='disabled' else 'administrative_reactivation')
        changed = db.execute('UPDATE account_lifecycle SET state=?,sequence=?,auth_generation=?,last_event_id=? WHERE user_id=2 AND sequence=? AND auth_generation=?',
                             (target,seq,gen,eid,expected_seq,expected_generation)).rowcount
        if changed != 1: raise ValueError('stale')
        db.commit()
    except Exception:
        db.rollback(); raise


def invalidate(db, target, kind, replacement=None):
    """Synthetic application policy, not a database-enforced invariant."""
    db.execute('BEGIN IMMEDIATE')
    try:
        a = db.execute('SELECT a.manuscript_id,a.review_round,a.reviewer_id,i.status FROM review_assignments a JOIN reviewer_invitations i ON i.assignment_id=a.assignment_id WHERE a.assignment_id=?',(target,)).fetchone()
        if not a or a[3] != 'pending': raise ValueError('not pending')
        if kind == 'superseded':
            b = db.execute('SELECT a.manuscript_id,a.review_round,a.reviewer_id,i.status FROM review_assignments a JOIN reviewer_invitations i ON i.assignment_id=a.assignment_id WHERE a.assignment_id=?',(replacement,)).fetchone()
            if not b or b[:2] != a[:2] or b[2] == a[2] or b[3] != 'pending': raise ValueError('invalid replacement')
            if db.execute('SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=?',(replacement,)).fetchone(): raise ValueError('invalidated replacement')
        db.execute('INSERT INTO reviewer_invitation_invalidations VALUES (?,?,?,?,?,?,?)',
                   ('inval-'+str(target),target,kind,replacement,3,STAMP,'replacement_selected' if kind=='superseded' else 'editorial_cancellation'))
        db.commit()
    except Exception:
        db.rollback(); raise


class LifecycleDesignValidation(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp(); self.addCleanup(self.fx.tearDown)
        self.fx.migrate()
        self.db = self.connect(); self.addCleanup(self.db.close)
        self.db.execute('DROP TABLE email_verification_challenges')
        self.db.execute('DROP TABLE user_email_verifications'); self.db.commit()
        fixture.load_migration('008_email_verification.py').migrate(self.fx.path)

    def connect(self):
        db=sqlite3.connect(self.fx.path,timeout=0.1)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def initialize(self): apply_design(self.db)

    def enroll(self):
        event(self.db,'event-1',1,None,'active',1,'initial_enrollment')
        self.db.execute("INSERT INTO account_lifecycle VALUES (2,'active',1,1,'event-1')")
        self.db.commit()

    def replacement(self, aid=3, mid=1, rnd=1, reviewer=4, status='pending'):
        self.db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (?,?,?,?,?,'assigned',?)",(aid,mid,reviewer,3,rnd,STAMP))
        self.db.execute('INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (?,?,?)',(aid,status,STAMP))
        self.db.commit()

    def test_apply_twice_preserves_history_and_empty_tables(self):
        before=fixture.EmailOutboxMigrationTest.contents(self.db)
        apply_design(self.db); apply_design(self.db)
        after=fixture.EmailOutboxMigrationTest.contents(self.db)
        self.assertEqual(before,{k:after[k] for k in before})
        self.assertTrue(all(not after[k] for k in TABLES))
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertTrue(set(OBJECTS)<=set(dict(self.db.execute('SELECT name,sql FROM sqlite_master'))))

    def test_missing_prerequisite_partial_and_mismatch(self):
        self.db.execute("DELETE FROM schema_migrations WHERE migration_id='008_email_verification'"); self.db.commit()
        with self.assertRaises(ValueError): apply_design(self.db)
        self.db.execute("INSERT INTO schema_migrations VALUES ('008_email_verification',?)",(STAMP,));self.db.commit()
        self.db.execute('CREATE VIEW account_lifecycle AS SELECT 1');self.db.commit()
        with self.assertRaises(ValueError): apply_design(self.db)
        self.db.execute('DROP VIEW account_lifecycle');self.db.commit()
        self.initialize(); self.db.execute('DROP INDEX ix_reviewer_invalidation_actor_time');self.db.commit()
        with self.assertRaises(ValueError): apply_design(self.db)

    def test_failure_midway_rolls_back_all_objects(self):
        with self.assertRaises(RuntimeError): apply_design(self.db,fail_at=2)
        objects={r[0] for r in self.db.execute('SELECT name FROM sqlite_master')}
        self.assertFalse(set(OBJECTS)&objects)
        self.assertIsNone(self.db.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?',(DESIGN_ID,)).fetchone())

    def test_lifecycle_constraints_composite_fk_and_immutable_events(self):
        self.initialize(); self.enroll()
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE account_lifecycle SET state='disabled'")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE account_lifecycle_events SET actor_user_id=1")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute('DELETE FROM account_lifecycle_events')
        with self.assertRaises(sqlite3.IntegrityError): event(self.db,'duplicate',1,None,'active',1,'initial_enrollment')
        with self.assertRaises(sqlite3.IntegrityError): event(self.db,'bad',2,'disabled','disabled',2,'administrative_disable')
        self.db.rollback()

    def test_sequence_generation_and_stale_policy(self):
        self.initialize();self.assertIsNone(self.db.execute('SELECT * FROM account_lifecycle').fetchone());self.enroll()
        transition(self.db,1,1,'disabled'); transition(self.db,2,2,'active')
        self.assertEqual(self.db.execute('SELECT state,sequence,auth_generation FROM account_lifecycle').fetchone(),('active',3,3))
        with self.assertRaises(ValueError): transition(self.db,1,1,'disabled')
        with self.assertRaises(ValueError): transition(self.db,3,3,'active')
        self.assertEqual(self.db.execute('SELECT count(*) FROM account_lifecycle_events').fetchone()[0],3)
        self.assertNotEqual(1,self.db.execute('SELECT auth_generation FROM account_lifecycle').fetchone()[0])

    def test_lifecycle_competing_writers(self):
        self.initialize();self.enroll();self.db.execute('BEGIN IMMEDIATE')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError): transition(other,1,1,'disabled')
        self.db.rollback();transition(self.db,1,1,'disabled')
        with self.connect() as other:
            with self.assertRaises(ValueError): transition(other,1,1,'disabled')

    def test_original_check_allows_null_transition_and_sequence_gap(self):
        self.initialize();self.enroll()
        # SQLite CHECK passes NULL; the draft permits this malformed event.
        event(self.db,'gap',5,None,'disabled',1,'administrative_disable');self.db.commit()
        self.assertEqual(self.db.execute("SELECT sequence,from_state,auth_generation FROM account_lifecycle_events WHERE event_id='gap'").fetchone(),(5,None,1))

    def test_corrected_null_check_on_disposable_table(self):
        self.db.execute("""CREATE TABLE corrected_transition (
            seq INTEGER NOT NULL, from_state TEXT, to_state TEXT NOT NULL, reason TEXT NOT NULL,
            CHECK ((seq=1 AND from_state IS NULL AND to_state='active' AND reason='initial')
                OR (seq>1 AND from_state IS NOT NULL AND from_state='active' AND to_state='disabled' AND reason='disable')
                OR (seq>1 AND from_state IS NOT NULL AND from_state='disabled' AND to_state='active' AND reason='reactivate')))
        """)
        for row in ((1,None,'active','initial'),(2,'active','disabled','disable'),(3,'disabled','active','reactivate')):
            self.db.execute('INSERT INTO corrected_transition VALUES (?,?,?,?)',row)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO corrected_transition VALUES (?,?,?,?)',(2,None,'disabled','disable'))
        self.db.rollback()

    def test_nonexistent_actor_user_and_replacement_are_foreign_key_rejected(self):
        self.initialize()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO account_lifecycle_events VALUES ('x',2,1,NULL,'active',1,999,?,'initial_enrollment')",(STAMP,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('x',1,'superseded',999,3,?,'replacement_selected')",(STAMP,))
        self.db.rollback()

    def test_pending_cancel_duplicate_and_append_only(self):
        self.initialize();invalidate(self.db,1,'cancelled')
        with self.assertRaises(sqlite3.IntegrityError): invalidate(self.db,1,'cancelled')
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute('DELETE FROM reviewer_invitation_invalidations')
        self.assertEqual(self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0],'pending')

    def test_valid_supersession_and_invalid_replacements(self):
        self.initialize(); self.replacement();invalidate(self.db,1,'superseded',3)
        self.assertEqual(self.db.execute('SELECT replacement_assignment_id FROM reviewer_invitation_invalidations').fetchone()[0],3)

    def test_replacement_policy_rejects_cross_round_manuscript_and_reviewer(self):
        self.initialize()
        self.db.execute("INSERT INTO manuscripts(manuscript_id,title,abstract,author_id,status,review_round,created_at) VALUES (2,'Synthetic','Synthetic',1,'revised',1,?)",(STAMP,));self.db.commit()
        for aid,mid,rnd,reviewer in ((3,2,1,4),(4,1,2,4),(5,1,1,2)):
            self.replacement(aid,mid,rnd,reviewer)
            with self.assertRaises(ValueError): invalidate(self.db,1,'superseded',aid)
        with self.assertRaises(ValueError): invalidate(self.db,1,'superseded',999)

    def test_direct_sql_can_bypass_replacement_and_accepted_policy(self):
        self.initialize(); self.replacement(mid=1,rnd=2)
        self.db.execute("UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=1")
        self.db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('unsafe',1,'superseded',3,3,?,'replacement_selected')",(STAMP,));self.db.commit()
        self.assertEqual(self.db.execute('SELECT kind FROM reviewer_invitation_invalidations').fetchone()[0],'superseded')

    def test_accepted_and_declined_rejected_by_policy(self):
        self.initialize()
        for status in ('accepted','declined'):
            self.db.execute('UPDATE reviewer_invitations SET status=? WHERE assignment_id=1',(status,));self.db.commit()
            with self.assertRaises(ValueError): invalidate(self.db,1,'cancelled')

    def test_lock_serializes_response_and_cancellation_but_current_response_can_bypass(self):
        self.initialize();self.db.execute('BEGIN IMMEDIATE')
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError): invalidate(other,1,'cancelled')
        self.db.rollback();invalidate(self.db,1,'cancelled')
        # Current response update does not inspect invalidation, demonstrating a gap.
        self.db.execute("UPDATE reviewer_invitations SET status='accepted' WHERE assignment_id=1 AND status='pending'");self.db.commit()
        self.assertEqual(self.db.execute('SELECT status FROM reviewer_invitations WHERE assignment_id=1').fetchone()[0],'accepted')

    def test_started_and_unknown_attempt_preserved_by_invalidation(self):
        self.initialize();self.fx.insert_event(self.db);self.fx.insert_outbox(self.db,status='processing',due=None,token='synthetic-lease',lease=STAMP);self.fx.insert_attempt(self.db);self.db.commit()
        invalidate(self.db,1,'cancelled')
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0],'started')
        self.db.execute("UPDATE email_delivery_attempts SET outcome='unknown',finished_at=?",(STAMP,));self.db.commit()
        self.assertEqual(self.db.execute('SELECT outcome FROM email_delivery_attempts').fetchone()[0],'unknown')


if __name__ == '__main__': unittest.main()
