"""Migration 007 checks using synthetic data in disposable SQLite databases only."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect
from app.database import Base
from app import models
from app.email.transports import InMemoryTransport

ROOT = Path(__file__).resolve().parents[1]
NEW = {'notification_events', 'email_outbox', 'email_delivery_attempts'}
LATER = {'review_round_files', 'review_assignment_files', 'reviewer_invitations',
         'manuscript_review_policies', 'author_agreements', 'final_editor_invitations',
         'final_editing_submission_invitations', 'formatter_invitations',
         'formatting_submissions', 'prototype_publications', 'account_lifecycle_events',
         'account_lifecycle', 'reviewer_invitation_invalidations'} | NEW
STAMP = '2026-01-02T03:04:05.000000Z'


def load_migration(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('.', '_'), ROOT / 'migrations' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATIONS = [load_migration(name) for name in (
    '001_reviewer_file_bindings.py', '002_reviewer_invitations.py',
    '003_two_reviewer_policy.py', '004_author_agreements.py',
    '005_final_editor_workflow.py', '006_formatter_publication.py', '007_email_outbox.py')]


class EmailOutboxMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gsub-outbox-')
        self.path = Path(self.temp.name) / 'test.sqlite'
        self.engine = create_engine(f'sqlite:///{self.path}')
        Base.metadata.create_all(self.engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in LATER])
        self.engine.dispose()
        for migration in MIGRATIONS[:-1]:
            migration.migrate(self.path)
        with self.connect() as db:
            db.execute("INSERT INTO users(user_id,name,email,password_hash,role,created_at) VALUES (1,'Author','author@example.com','x','author',?), (2,'Reviewer','reviewer@example.com','x','reviewer',?), (3,'Editor','editor@example.com','x','editor',?), (4,'Reviewer Two','reviewer2@example.com','x','reviewer',?)", (STAMP, STAMP, STAMP, STAMP))
            db.execute("INSERT INTO manuscripts(manuscript_id,title,abstract,author_id,status,review_round,created_at) VALUES (1,'Synthetic','Fixture',1,'ready_for_peer_review',1,?)", (STAMP,))
            db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (1,1,2,3,1,'assigned',?)", (STAMP,))
            db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (2,1,4,3,1,'completed',?)", (STAMP,))
            db.execute("INSERT INTO reviews(review_id,assignment_id,comments,recommendation,submitted_at) VALUES (1,2,'Synthetic review','accept',?)", (STAMP,))
            db.execute("INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (1,'pending',?)", (STAMP,))
            db.execute("INSERT INTO notifications(notification_id,user_id,manuscript_id,notification_type,message,is_read,created_at) VALUES (1,2,1,'review_invitation','Synthetic',0,?)", (STAMP,))
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def contents(db):
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'schema_migrations' ORDER BY name")]
        return {name: sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in tables}

    def migrate(self):
        MIGRATIONS[-1].migrate(self.path)

    def insert_event(self, db, event='event-1', kind='reviewer.invitation.created', assignment=1, version=1):
        db.execute('INSERT INTO notification_events VALUES (?,?,?,?,?)', (event, kind, assignment, STAMP, version))

    def insert_outbox(self, db, oid='out-1', event='event-1', recipient=2, status='pending', attempts=0, due=STAMP, token=None, lease=None, accepted=None):
        db.execute('''INSERT INTO email_outbox(outbox_id,event_id,recipient_user_id,template_key,template_version,status,attempt_count,next_attempt_at,lease_token,lease_until,created_at,updated_at,accepted_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (oid,event,recipient,'reviewer_invitation','v1',status,attempts,due,token,lease,STAMP,STAMP,accepted))

    def insert_attempt(self, db, aid='attempt-1', out='out-1', number=1, outcome='started', finished=None):
        db.execute('INSERT INTO email_delivery_attempts(attempt_id,outbox_id,attempt_number,lease_token,started_at,finished_at,outcome) VALUES (?,?,?,?,?,?,?)', (aid,out,number,'synthetic-lease',STAMP,finished,outcome))

    def test_additive_preservation_integrity_and_repeat(self):
        with self.connect() as db:
            before = self.contents(db)
            ledger_before = db.execute('SELECT * FROM schema_migrations ORDER BY migration_id').fetchall()
        with patch.object(InMemoryTransport, 'send', side_effect=AssertionError('transport must not run')):
            self.migrate()
            self.migrate()
        with self.connect() as db:
            after = self.contents(db)
            self.assertEqual(before, {name: after[name] for name in before})
            self.assertEqual(set(after) - set(before), NEW)
            self.assertEqual(ledger_before, db.execute("SELECT * FROM schema_migrations WHERE migration_id <> '007_email_outbox' ORDER BY migration_id").fetchall())
            for name in NEW:
                self.assertEqual(after[name], [])
            self.assertEqual(db.execute("SELECT count(*) FROM schema_migrations WHERE migration_id='007_email_outbox'").fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('SELECT assignment_id,status FROM reviewer_invitations').fetchall(), [(1, 'pending')])

    def test_models_match_migrated_schema(self):
        self.migrate()
        temp_engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(temp_engine)
        actual = inspect(create_engine(f'sqlite:///{self.path}'))
        expected = inspect(temp_engine)
        for name in NEW:
            with self.subTest(table=name):
                def columns(inspector):
                    return [(c['name'], str(c['type']), c['nullable'], c['primary_key'], c['default']) for c in inspector.get_columns(name)]
                self.assertEqual(columns(actual), columns(expected))
                def checks(inspector):
                    return {c['name']: ''.join(c['sqltext'].lower().split()) for c in inspector.get_check_constraints(name)}
                self.assertEqual(checks(actual), checks(expected))
                def fks(inspector):
                    with inspector.bind.connect() as connection:
                        return sorted((row[3], row[2], row[4], row[6]) for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list({name})'))
                self.assertEqual(fks(actual), fks(expected))
                self.assertEqual(sorted((i['name'], tuple(i['column_names']), i['unique']) for i in actual.get_indexes(name)), sorted((i['name'], tuple(i['column_names']), i['unique']) for i in expected.get_indexes(name)))
                self.assertEqual(sorted(tuple(c['column_names']) for c in actual.get_unique_constraints(name)), sorted(tuple(c['column_names']) for c in expected.get_unique_constraints(name)))
        actual.bind.dispose()
        temp_engine.dispose()

    def test_primary_foreign_keys_and_event_identity(self):
        self.migrate()
        with self.connect() as db:
            self.insert_event(db)
            cases = [lambda: self.insert_event(db, event='event-1', assignment=1),
                     lambda: self.insert_event(db, event='event-2', assignment=1),
                     lambda: self.insert_event(db, event='event-3', assignment=999),
                     lambda: self.insert_event(db, event='event-4', kind='invalid'),
                     lambda: self.insert_event(db, event='event-5', version=2),
                     lambda: self.insert_event(db, event=None)]
            for operation in cases:
                with self.subTest(operation=operation), self.assertRaises(sqlite3.IntegrityError): operation()
            self.insert_outbox(db)
            for args in (dict(oid='out-1'), dict(oid='out-2'), dict(oid='out-3',event='missing'), dict(oid='out-4',recipient=999), dict(oid=None)):
                with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError): self.insert_outbox(db, **args)
            self.insert_attempt(db)
            for args in (dict(aid='attempt-1'), dict(aid='attempt-2'), dict(aid='attempt-3',out='missing'), dict(aid=None)):
                with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError): self.insert_attempt(db, **args)

    def test_status_count_lease_and_due_constraints(self):
        self.migrate()
        with self.connect() as db:
            self.insert_event(db)
            cases = [dict(status='pending',due=STAMP), dict(status='retry_wait',due=STAMP),
                     dict(status='processing',due=None,token='lease',lease=STAMP),
                     dict(status='accepted',due=None,accepted=STAMP)] + [dict(status=s,due=None) for s in ('blocked_verification','failed','unknown','cancelled')]
            for number, case in enumerate(cases):
                with self.subTest(case=case):
                    db.execute('SAVEPOINT valid_case')
                    self.insert_outbox(db, oid=f'valid-{number}', **case)
                    db.execute('ROLLBACK TO valid_case')
                    db.execute('RELEASE valid_case')
            invalid = [dict(status='bad'), dict(attempts=-1), dict(attempts=1.5), dict(status='pending',due=None),
                       dict(status='processing',due=None), dict(status='processing',due=None,token='',lease=STAMP),
                       dict(status='pending',token='x',lease=STAMP), dict(status='failed',due=STAMP),
                       dict(status='accepted',due=None), dict(status='pending',accepted=STAMP)]
            for number, case in enumerate(invalid):
                with self.subTest(case=case), self.assertRaises(sqlite3.IntegrityError): self.insert_outbox(db,oid=f'invalid-{number}',**case)

    def test_attempt_number_outcome_and_completion_constraints(self):
        self.migrate()
        with self.connect() as db:
            self.insert_event(db)
            self.insert_outbox(db)
            for n, outcome in enumerate(('started','accepted','retryable','permanent','unknown'), 1):
                self.insert_attempt(db, aid=f'a-{n}', number=n, outcome=outcome, finished=None if outcome == 'started' else STAMP)
            for n, args in enumerate((dict(number=0), dict(number=-1), dict(number=1.5), dict(number=10,outcome='invalid'), dict(number=11,outcome='started',finished=STAMP), dict(number=12,outcome='accepted',finished=None)), 10):
                with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError): self.insert_attempt(db,aid=f'bad-{n}',**args)

    def test_timestamps_require_fixed_utc_shape(self):
        self.migrate()
        with self.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO notification_events VALUES (?,?,?,?,?)', ('bad','reviewer.invitation.created',1,'2026-01-02 03:04:05',1))
            self.insert_event(db)
            with self.assertRaises(sqlite3.IntegrityError):
                self.insert_outbox(db, due='2026-01-02T03:04:05+00:00')
            self.insert_outbox(db)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO email_delivery_attempts(attempt_id,outbox_id,attempt_number,lease_token,started_at,outcome) VALUES (?,?,?,?,?,?)', ('bad','out-1',1,'lease','not-a-time','started'))

    def test_audit_fields_are_bounded_and_parent_rows_restricted(self):
        self.migrate()
        with self.connect() as db:
            self.insert_event(db)
            self.insert_outbox(db)
            self.insert_attempt(db)
            cases = [
                ("UPDATE email_outbox SET terminal_reason=? WHERE outbox_id='out-1'", ('x' * 65,)),
                ("UPDATE email_outbox SET provider_message_id=? WHERE outbox_id='out-1'", ('x' * 256,)),
                ("UPDATE email_delivery_attempts SET error_code=? WHERE attempt_id='attempt-1'", ('x' * 65,)),
                ("UPDATE email_delivery_attempts SET lease_token='' WHERE attempt_id='attempt-1'", ()),
                ("DELETE FROM reviewer_invitations WHERE assignment_id=1", ()),
                ("DELETE FROM notification_events WHERE event_id='event-1'", ()),
                ("DELETE FROM email_outbox WHERE outbox_id='out-1'", ()),
            ]
            for sql, args in cases:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql, args)

    def test_startup_without_migration_007_does_not_create_tables(self):
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {'SECRET_KEY': 'temporary-test-only-key', 'MANUSCRIPT_MODE': 'development'}):
            import app.main as main
            isolated = create_engine(f'sqlite:///{self.path}')
            try:
                with patch.object(main, 'engine', isolated):
                    with TestClient(main.app) as client:
                        self.assertEqual(client.get('/health').status_code, 200)
                with self.connect() as db:
                    self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('notification_events','email_outbox','email_delivery_attempts')").fetchone())
                    self.assertFalse(db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='007_email_outbox'").fetchone())
            finally:
                isolated.dispose()

    def test_missing_prerequisite_and_conflicting_objects_leave_database_unchanged(self):
        with self.connect() as db:
            db.execute("DELETE FROM schema_migrations WHERE migration_id='005_final_editor_workflow'")
            db.commit()
            before = self.contents(db)
        with self.assertRaisesRegex(RuntimeError, '001 through 006'):
            self.migrate()
        with self.connect() as db:
            self.assertEqual(before, self.contents(db))
            db.execute("INSERT INTO schema_migrations VALUES ('005_final_editor_workflow',?)", (STAMP,))
            db.execute('CREATE VIEW email_outbox AS SELECT 1 AS unexpected')
            db.commit()
        with self.assertRaisesRegex(RuntimeError, 'without ledger'):
            self.migrate()
        with self.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='007_email_outbox'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_events'").fetchone())

    def test_partial_schema_after_ledger_rejected(self):
        self.migrate()
        with self.connect() as db:
            db.execute('DROP INDEX ix_email_outbox_due')
            db.commit()
        with self.assertRaisesRegex(RuntimeError, 'schema mismatch'):
            self.migrate()
        with self.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='ix_email_outbox_due'").fetchone())
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_failure_rolls_back_created_objects_and_ledger(self):
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute("INSERT INTO reviewer_invitations(assignment_id,status,invited_at) VALUES (999,'pending',?)", (STAMP,))
            db.commit()
        with self.assertRaisesRegex(RuntimeError, 'Foreign key check failed'):
            self.migrate()
        with self.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='007_email_outbox'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('notification_events','email_outbox','email_delivery_attempts')").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM reviewer_invitations').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
