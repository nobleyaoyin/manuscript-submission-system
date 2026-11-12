"""Synthetic 006→009 rehearsal: disposable SQLite; no real uploads or email."""
import os
import hashlib
import subprocess
import sys
import sqlite3
import tempfile
from pathlib import Path
import unittest
from importlib import import_module
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'synthetic-demo-upgrade-secret')
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.routers import users
from app.schemas import UserLogin
from app.email.enqueue_mode import EnqueueMode, reviewer_email_enqueue_mode
from app.email.transports import InMemoryTransport
from tests import test_email_outbox_migration as fixture

m7=import_module('migrations.007_email_outbox')
m8=import_module('migrations.008_email_verification')
m9=import_module('migrations.009_account_invitation_lifecycle')


class SyntheticDemoUpgrade(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.EmailOutboxMigrationTest('test_additive_preservation_integrity_and_repeat')
        self.fx.setUp();self.addCleanup(self.fx.tearDown)
        # Base metadata includes 008 models; remove only the empty synthetic
        # fixture tables to reproduce a ledger/schema with 001–006 only.
        with self.fx.connect() as db:
            db.execute('DROP TABLE email_verification_challenges')
            db.execute('DROP TABLE user_email_verifications');db.commit()
        self.engine=create_engine(f'sqlite:///{self.fx.path}')
        self.addCleanup(self.engine.dispose)
        self.Session=sessionmaker(bind=self.engine,expire_on_commit=False)

    def contents(self):
        with self.fx.connect() as db:return self.fx.contents(db)

    def ledger(self):
        with self.fx.connect() as db:return {r[0] for r in db.execute('SELECT migration_id FROM schema_migrations')}

    def login(self):
        with self.Session() as db, patch.object(users,'IS_DEMO',True), \
             patch.object(users,'limit_login'), patch.object(users,'verify_password',return_value=True), \
             patch.object(users,'create_access_token',return_value='synthetic-token'), \
             patch.object(users,'decode_access_token',return_value=2):
            result=users.login_user(UserLogin(email='reviewer@example.com',password='synthetic-password'),None,db)
            user=users.get_current_user(HTTPAuthorizationCredentials(scheme='Bearer',credentials='synthetic-token'),db)
            return result['token_type'],user.user_id

    def test_order_upgrade_preservation_idempotency_and_demo_login(self):
        before=self.contents();before_ledger=self.ledger()
        self.assertEqual(len(before_ledger),6)
        self.assertEqual(self.login(),('bearer',2))
        with patch('socket.socket',side_effect=AssertionError('network forbidden')), \
             patch.object(InMemoryTransport,'send',side_effect=AssertionError('transport forbidden')):
            for migration in (m7,m8,m9):
                migration.migrate(self.fx.path);migration.migrate(self.fx.path)
                current=self.contents()
                self.assertEqual(before,{k:current[k] for k in before})
                self.assertEqual(self.login(),('bearer',2))
        self.assertEqual(reviewer_email_enqueue_mode(),EnqueueMode.DISABLED)
        final=self.contents()
        for table in ('notification_events','email_outbox','email_delivery_attempts','user_email_verifications',
                      'email_verification_challenges','account_lifecycle_events','account_lifecycle','reviewer_invitation_invalidations'):
            self.assertEqual(final[table],[],table)
        self.assertEqual(self.ledger(),before_ledger|{m7.MIGRATION_ID,m8.MIGRATION_ID,m9.MIGRATION_ID})
        with self.fx.connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_wrong_order_is_rejected_without_new_schema(self):
        before=self.contents();ledger=self.ledger()
        with self.assertRaises(RuntimeError):m8.migrate(self.fx.path)
        with self.assertRaises(RuntimeError):m9.migrate(self.fx.path)
        self.assertEqual(before,self.contents());self.assertEqual(ledger,self.ledger())

    def test_fresh_real_initializer_then_isolated_upgrade(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='synthetic-demo-init-') as temporary:
            parent = Path(temporary).resolve()
            root = parent / 'demo'
            # A subprocess-wide network guard makes real egress fail closed.
            (parent / 'sitecustomize.py').write_text(
                "import socket\n"
                "def denied(*args, **kwargs): raise AssertionError('network forbidden')\n"
                "socket.create_connection=denied\n"
                "socket.socket.connect=denied\n"
                "socket.socket.connect_ex=denied\n"
            )
            env = os.environ.copy()
            env['PYTHONPATH'] = str(parent)
            env['SECRET_KEY'] = 'synthetic-demo-only-signing-key'
            cmd = [sys.executable, '-B', str(project / 'scripts/init_demo.py'), '--data-root', str(root)]
            result = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, 'Synthetic initializer failed')
            path = root / 'manuscript.db'
            private_hashes = {name: hashlib.sha256((root / name).read_bytes()).digest()
                              for name in ('accounts.json', 'jwt.key')}
            with sqlite3.connect(path) as db:
                before = fixture.EmailOutboxMigrationTest.contents(db)
                ledger = {r[0] for r in db.execute('SELECT migration_id FROM schema_migrations')}
                expected = {m.MIGRATION_ID if hasattr(m, 'MIGRATION_ID') else m.ID
                            for m in fixture.MIGRATIONS[:-1]}
                self.assertEqual(ledger, expected)
                objects = {r[0] for r in db.execute('SELECT name FROM sqlite_master')}
                for migration in (m7, m8, m9):
                    self.assertFalse(set(migration.DDL) & objects)
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 5)
                self.assertEqual(db.execute('SELECT count(*) FROM manuscripts').fetchone()[0], 1)
            # Refuse to overwrite even this synthetic root.
            repeated = subprocess.run(cmd, cwd=project, env=env, capture_output=True, timeout=60)
            self.assertNotEqual(repeated.returncode, 0)
            with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                 patch.object(InMemoryTransport, 'send', side_effect=AssertionError('transport forbidden')):
                for migration in (m7, m8, m9):
                    migration.migrate(path); migration.migrate(path)
                    with sqlite3.connect(path) as db:
                        after = fixture.EmailOutboxMigrationTest.contents(db)
                        self.assertEqual(before, {k: after[k] for k in before})
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                ledger = {r[0] for r in db.execute('SELECT migration_id FROM schema_migrations')}
                self.assertEqual(ledger, expected | {m7.MIGRATION_ID, m8.MIGRATION_ID, m9.MIGRATION_ID})
                for table in ('notification_events', 'email_outbox', 'email_delivery_attempts',
                              'user_email_verifications', 'email_verification_challenges',
                              'account_lifecycle_events', 'account_lifecycle', 'reviewer_invitation_invalidations'):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0], 0)
            self.assertEqual(private_hashes, {name: hashlib.sha256((root / name).read_bytes()).digest()
                                              for name in private_hashes})

    def test_008_failure_rolls_back_its_tables_and_ledger(self):
        m7.migrate(self.fx.path);before=self.contents();ledger=self.ledger()
        invalid=dict(m8.DDL);invalid['synthetic_failure']='CREATE TABLE bad syntax('
        with patch.object(m8,'DDL',invalid),self.assertRaises(sqlite3.OperationalError):m8.migrate(self.fx.path)
        self.assertEqual(before,self.contents());self.assertEqual(ledger,self.ledger())


if __name__=='__main__':unittest.main()
