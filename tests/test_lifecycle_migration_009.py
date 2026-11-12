"""Migration 009 tests: disposable SQLite databases and synthetic records only."""
from importlib import import_module
import sqlite3
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, inspect
from app.database import Base
from tests import test_email_outbox_migration as fixture

migration = import_module('migrations.009_account_invitation_lifecycle')
TABLES = {'account_lifecycle_events','account_lifecycle','reviewer_invitation_invalidations'}
STAMP = fixture.STAMP


class LifecycleMigrationTest(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp();self.addCleanup(self.fx.tearDown);self.fx.migrate()
        with self.fx.connect() as db:
            db.execute('DROP TABLE email_verification_challenges')
            db.execute('DROP TABLE user_email_verifications');db.commit()
        fixture.load_migration('008_email_verification.py').migrate(self.fx.path)

    def connect(self): return self.fx.connect()
    def migrate(self): migration.migrate(self.fx.path)

    def event(self, db, eid='event', seq=1, before=None, after='active', gen=1, reason='initial_enrollment'):
        db.execute('INSERT INTO account_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?)',(eid,2,seq,before,after,gen,3,STAMP,reason))

    def test_additive_idempotent_and_preserves_every_old_table(self):
        with self.connect() as db:
            before=self.fx.contents(db);ledger=db.execute('SELECT * FROM schema_migrations ORDER BY migration_id').fetchall()
        self.migrate();self.migrate()
        with self.connect() as db:
            after=self.fx.contents(db)
            self.assertEqual(before,{k:after[k] for k in before})
            self.assertEqual(set(after)-set(before),TABLES)
            self.assertTrue(all(after[name]==[] for name in TABLES))
            self.assertEqual(ledger,db.execute('SELECT * FROM schema_migrations WHERE migration_id<>? ORDER BY migration_id',(migration.MIGRATION_ID,)).fetchall())
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations WHERE migration_id=?',(migration.MIGRATION_ID,)).fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_schema_matches_corrected_design(self):
        from tests import test_corrected_lifecycle_design as design
        self.assertEqual({k:migration._normalize(v) for k,v in migration.DDL.items()},
                         {k:migration._normalize(v) for k,v in design.OBJECTS.items()})

    def test_sqlalchemy_model_schema_parity(self):
        self.migrate(); memory=create_engine('sqlite:///:memory:');actual_engine=create_engine(f'sqlite:///{self.fx.path}')
        self.addCleanup(memory.dispose);self.addCleanup(actual_engine.dispose)
        Base.metadata.create_all(memory)
        a=inspect(actual_engine); b=inspect(memory)
        for table in TABLES:
            with self.subTest(table=table):
                def cols(inspector): return [(c['name'],str(c['type']),c['nullable'],c['primary_key'],c['default']) for c in inspector.get_columns(table)]
                self.assertEqual(cols(a),cols(b))
                def checks(inspector):return sorted(''.join(c['sqltext'].lower().split()) for c in inspector.get_check_constraints(table))
                self.assertEqual(checks(a),checks(b))
                def fks(inspector):
                    with inspector.bind.connect() as connection:
                        rows = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').fetchall()
                    groups = {}
                    for row in rows:
                        groups.setdefault(row[0], []).append(row)
                    return sorted((tuple(x[3] for x in sorted(group,key=lambda x:x[1])), group[0][2],
                                   tuple(x[4] for x in sorted(group,key=lambda x:x[1])), group[0][6])
                                  for group in groups.values())
                self.assertEqual(fks(a),fks(b))
                def indexes(inspector):
                    return sorted((x['name'],tuple(x['column_names']),x['unique'],str(x.get('dialect_options',{}).get('sqlite_where',''))) for x in inspector.get_indexes(table))
                self.assertEqual(indexes(a),indexes(b))
                def uniques(inspector):return sorted(tuple(x['column_names']) for x in inspector.get_unique_constraints(table))
                self.assertEqual(uniques(a),uniques(b))
        # Triggers are migration-managed rather than SQLAlchemy metadata.
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('lifecycle_events_no_update','lifecycle_events_no_delete','reviewer_invalidations_no_update','reviewer_invalidations_no_delete')").fetchone()[0],4)

    def test_null_invalid_transitions_unique_and_composite_fk(self):
        self.migrate()
        with self.connect() as db:
            self.event(db)
            db.execute("INSERT INTO account_lifecycle VALUES (2,'active',1,1,'event')")
            for args in (dict(eid='bad-null',seq=2,before=None,after='disabled',gen=2,reason='administrative_disable'),
                         dict(eid='bad-transition',seq=2,before='disabled',after='disabled',gen=2,reason='administrative_disable'),
                         dict(eid='duplicate',seq=1)):
                with self.subTest(args=args),self.assertRaises(sqlite3.IntegrityError):self.event(db,**args)
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE account_lifecycle SET state='disabled'")
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM account_lifecycle_events')
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE account_lifecycle_events SET actor_user_id=1")
            db.rollback()

    def test_invalidation_constraints_and_immutable_triggers(self):
        self.migrate()
        with self.connect() as db:
            db.execute("INSERT INTO reviewer_invitations VALUES (2,'pending',?,NULL)",(STAMP,))
            db.execute("INSERT INTO review_assignments(assignment_id,manuscript_id,reviewer_id,assigned_by,review_round,status,assigned_at) VALUES (3,1,4,3,1,'assigned',?)",(STAMP,))
            db.execute("INSERT INTO reviewer_invitations VALUES (3,'pending',?,NULL)",(STAMP,))
            db.execute("INSERT INTO reviewer_invitation_invalidations VALUES ('i1',1,'superseded',2,3,?,'replacement_selected')",(STAMP,))
            for sql,args in [("INSERT INTO reviewer_invitation_invalidations VALUES ('dup',1,'cancelled',NULL,3,?,'editorial_cancellation')",(STAMP,)),
                             ("INSERT INTO reviewer_invitation_invalidations VALUES ('same',3,'superseded',2,3,?,'replacement_selected')",(STAMP,)),
                             ("INSERT INTO reviewer_invitation_invalidations VALUES ('missing',3,'superseded',999,3,?,'replacement_selected')",(STAMP,)),
                             ("INSERT INTO reviewer_invitation_invalidations VALUES ('bad',3,'cancelled',NULL,3,?,'replacement_selected')",(STAMP,)),
                             ('DELETE FROM reviewer_invitation_invalidations',()),('UPDATE reviewer_invitation_invalidations SET actor_user_id=1',())]:
                with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):db.execute(sql,args)
            db.rollback()

    def test_missing_and_incompatible_prerequisites(self):
        with self.connect() as db:db.execute("DELETE FROM schema_migrations WHERE migration_id='008_email_verification'");db.commit()
        with self.assertRaisesRegex(RuntimeError,'001 through 008'):self.migrate()
        with self.connect() as db:
            db.execute("INSERT INTO schema_migrations VALUES ('008_email_verification',?)",(STAMP,))
            db.execute('DROP INDEX ix_email_verification_challenges_user');db.commit()
        with self.assertRaisesRegex(RuntimeError,'prerequisite schema'):self.migrate()
        with self.connect() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='account_lifecycle'").fetchone())

    def test_partial_conflicting_and_extra_schema_rejected(self):
        with self.connect() as db:db.execute('CREATE VIEW account_lifecycle AS SELECT 1');db.commit()
        with self.assertRaisesRegex(RuntimeError,'without ledger'):self.migrate()
        with self.connect() as db:db.execute('DROP VIEW account_lifecycle');db.commit()
        self.migrate()
        with self.connect() as db:db.execute('DROP TRIGGER lifecycle_events_no_update');db.commit()
        with self.assertRaisesRegex(RuntimeError,'schema mismatch'):self.migrate()

    def test_unexpected_object_and_changed_literal_are_rejected(self):
        self.migrate()
        with self.connect() as db:
            db.execute('CREATE INDEX unexpected_lifecycle_index ON account_lifecycle(state)');db.commit()
        with self.assertRaisesRegex(RuntimeError,'Unexpected lifecycle'):self.migrate()
        with self.connect() as db:
            db.execute('DROP INDEX unexpected_lifecycle_index')
            db.execute('DROP TRIGGER lifecycle_events_no_update')
            db.execute("CREATE TRIGGER lifecycle_events_no_update BEFORE UPDATE ON account_lifecycle_events BEGIN SELECT RAISE(ABORT, 'Lifecycle events are append-only'); END")
            db.commit()
        with self.assertRaisesRegex(RuntimeError,'schema mismatch'):self.migrate()

    def test_transactional_failure_rolls_back_all_created_objects(self):
        altered=dict(migration.DDL);altered['injected_invalid']='CREATE TABLE bad syntax('
        with patch.object(migration,'DDL',altered),self.assertRaises(sqlite3.OperationalError):self.migrate()
        with self.connect() as db:
            names={r[0] for r in db.execute('SELECT name FROM sqlite_master')}
            self.assertFalse(TABLES & names)
            self.assertIsNone(db.execute('SELECT 1 FROM schema_migrations WHERE migration_id=?',(migration.MIGRATION_ID,)).fetchone())


if __name__ == '__main__':unittest.main()
