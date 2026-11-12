"""Invitation enqueue integration: disposable SQLite, uploads and in-process API only."""
import hashlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'isolated-test-only-secret')

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, Session

from app.database import Base, get_db
from app import models
import app.main as main
import app.review_files as review_files
import app.routers.manuscripts as manuscripts
import app.routers.assignments as assignments
from app.routers.users import get_current_user
from app.email.enqueue_mode import EnqueueMode, reviewer_email_enqueue_mode
from app.email.transports import InMemoryTransport
from app.email import templates
from tests import test_email_outbox_migration as migration_fixture


class InvitationEmailIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gsub-enqueue-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'fixture.sqlite'
        self.engine = create_engine(f'sqlite:///{self.path}', connect_args={'check_same_thread': False, 'timeout': .2})
        @event.listens_for(self.engine, 'connect')
        def fk(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in migration_fixture.LATER])
        for migration in migration_fixture.MIGRATIONS[:-1]: migration.migrate(self.path)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        with self.Session() as db:
            users = [models.User(name=name, email=f'{name.lower()}@example.com', password_hash='x', role=role) for name, role in [('Author','author'),('Editor','editor'),('Eic','eic'),('Reviewer','reviewer'),('Other','reviewer'),('Unassigned','editor')]]
            db.add_all(users); db.flush()
            self.ids = {u.name: u.user_id for u in users}
            m = models.Manuscript(title='Synthetic', abstract='Synthetic', author_id=self.ids['Author'], status='ready_for_peer_review', review_round=1)
            db.add(m); db.flush(); self.mid = m.manuscript_id
            path = self.root / 'uploads' / 'initial_editing' / str(self.mid) / 'confirmed.pdf'
            path.parent.mkdir(parents=True); path.write_bytes(b'%PDF-synthetic-confirmed')
            self.pdf = path
            rel = str(path.relative_to(self.root))
            submission = models.InitialEditingSubmission(manuscript_id=self.mid, editor_id=self.ids['Editor'], file_path=rel, comments='Synthetic', status='eic_approved')
            db.add(submission); db.flush()
            db.add(models.PreReviewConfirmation(manuscript_id=self.mid, submission_id=submission.submission_id, author_id=self.ids['Author'], file_path=rel, decision='confirm', comments='Synthetic'))
            db.add(models.InitialEditorInvitation(manuscript_id=self.mid, editor_id=self.ids['Editor'], invited_by=self.ids['Eic'], status='accepted'))
            db.commit()
        self.actor = SimpleNamespace(user_id=self.ids['Editor'], role='editor', name='Editor')
        def get_temp_db():
            with self.Session() as db: yield db
        main.app.dependency_overrides[get_db] = get_temp_db
        main.app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(main.app.dependency_overrides.clear)
        self.patches = [patch.object(main, 'engine', self.engine), patch.object(review_files, 'BASE_DIR', self.root), patch.object(manuscripts, 'BASE_DIR', self.root), patch.object(InMemoryTransport, 'send', side_effect=AssertionError('transport forbidden')), patch.object(templates, 'render_template', side_effect=AssertionError('template forbidden')), patch('socket.create_connection', side_effect=AssertionError('external network forbidden')), patch('socket.socket.connect', side_effect=AssertionError('external network forbidden'))]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)

    def schema(self): migration_fixture.MIGRATIONS[-1].migrate(self.path)
    def enable(self): main.app.dependency_overrides[reviewer_email_enqueue_mode] = lambda: EnqueueMode.ENQUEUE_ONLY
    def invite(self, reviewer='Reviewer', round_no=1, **kwargs):
        return self.client.post('/assignments/', json={'manuscript_id':self.mid,'reviewer_id':self.ids[reviewer],'review_round':round_no}, **kwargs)
    def rows(self, table):
        with sqlite3.connect(self.path) as db: return db.execute(f'SELECT * FROM "{table}"').fetchall()
    def hash(self): return hashlib.sha256(self.pdf.read_bytes()).hexdigest()

    def assert_no_new_records(self, with_outbox=True):
        for table in ('review_round_files','review_assignments','review_assignment_files','reviewer_invitations','notifications'):
            self.assertEqual(self.rows(table), [], table)
        if with_outbox:
            for table in ('notification_events','email_outbox','email_delivery_attempts'): self.assertEqual(self.rows(table), [], table)

    def test_default_disabled_and_untrusted_request_input_on_001_006(self):
        with patch.object(assignments, 'check_outbox_schema', side_effect=AssertionError('preflight forbidden')), patch.object(assignments, 'enqueue_reviewer_invitation', side_effect=AssertionError('enqueue forbidden')):
            response = self.invite(headers={'X-Email-Enqueue': 'enqueue_only'}, params={'enqueue_mode': 'enqueue_only'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(self.rows('notifications')), 1)
        with sqlite3.connect(self.path) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='email_outbox'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM schema_migrations WHERE migration_id='007_email_outbox'").fetchone())

    def test_authorization_and_closed_unknown_mode(self):
        self.actor = SimpleNamespace(user_id=self.ids['Author'],role='author',name='Author')
        self.assertEqual(self.invite().status_code, 403)
        self.actor = SimpleNamespace(user_id=self.ids['Unassigned'],role='editor',name='Unassigned')
        self.assertEqual(self.invite().status_code, 403)
        self.actor = SimpleNamespace(user_id=self.ids['Eic'],role='eic',name='Eic')
        main.app.dependency_overrides[reviewer_email_enqueue_mode] = lambda: 'invalid'
        self.assertEqual(self.invite().status_code, 503)
        main.app.dependency_overrides.pop(reviewer_email_enqueue_mode)
        self.assertEqual(self.invite().status_code, 201)

    def test_missing_and_incompatible_schema_before_first_write(self):
        self.enable()
        original = self.hash()
        response = self.invite()
        self.assertEqual(response.status_code, 503); self.assertIn('not ready', response.json()['detail'])
        self.assert_no_new_records(False); self.assertEqual(self.hash(), original)
        self.schema()
        with sqlite3.connect(self.path) as db: db.execute('DROP INDEX ix_email_outbox_due'); db.commit()
        response = self.invite()
        self.assertEqual(response.status_code, 503)
        self.assert_no_new_records(); self.assertEqual(self.hash(), original)

    def test_disabled_foreign_keys_fails_closed(self):
        self.schema(); self.enable()
        nofk = create_engine(f'sqlite:///{self.path}', connect_args={'check_same_thread':False})
        self.addCleanup(nofk.dispose)
        def nofk_db():
            with Session(nofk) as db: yield db
        main.app.dependency_overrides[get_db] = nofk_db
        response = self.invite()
        self.assertEqual(response.status_code, 503); self.assertIn('not ready', response.json()['detail'])
        self.assert_no_new_records()

    def test_enabled_success_original_response_and_privacy(self):
        self.schema(); self.enable()
        original_commit = Session.commit
        with patch.object(Session, 'commit', autospec=True, side_effect=original_commit) as commit:
            response = self.invite()
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(response.status_code, 201, response.text)
        data=response.json(); aid=data['assignment_id']
        self.assertEqual(set(data), {'assignment_id','manuscript_id','reviewer_id','assigned_by','review_round','status','invitation_status','assigned_at','manuscript_title','manuscript_status','has_pdf','review_file_available','review_file_status'})
        self.assertEqual(data['invitation_status'], 'pending')
        self.assertEqual(data['review_round'], 1)
        for table in ('review_round_files','review_assignments','review_assignment_files','reviewer_invitations','notifications','notification_events','email_outbox'):
            self.assertEqual(len(self.rows(table)), 1, table)
        self.assertEqual(self.rows('email_delivery_attempts'), [])
        with self.Session() as db:
            event_row=db.scalar(select(models.NotificationEvent)); out=db.scalar(select(models.EmailOutbox))
            self.assertEqual((event_row.invitation_assignment_id,out.event_id,out.recipient_user_id), (aid,event_row.event_id,self.ids['Reviewer']))
            binding=db.get(models.ReviewAssignmentFile,aid); round_file=db.get(models.ReviewRoundFile,binding.round_file_id)
            self.assertEqual((round_file.source_kind,round_file.review_round), ('initial_editing',1))
            self.assertEqual(out.template_key,'reviewer_invitation')
        self.assertNotIn('example.com', repr(self.rows('email_outbox')))
        self.assertNotIn('Synthetic', repr(self.rows('notification_events')))

    def test_failures_roll_back_round_binding_and_notifications(self):
        self.schema(); self.enable(); original = self.hash()
        for table in ('review_assignments','notification_events','email_outbox'):
            def fail(conn, cursor, statement, parameters, context, many):
                if statement.lower().lstrip().startswith(f'insert into {table}'):
                    raise IntegrityError('synthetic', {}, sqlite3.IntegrityError('synthetic'))
            event.listen(self.engine, 'before_cursor_execute', fail)
            try: response = self.invite()
            finally: event.remove(self.engine, 'before_cursor_execute', fail)
            self.assertEqual(response.status_code, 503); self.assertIn('not saved', response.json()['detail'])
            self.assert_no_new_records(); self.assertEqual(self.hash(),original)
        with patch.object(assignments, 'enqueue_reviewer_invitation', side_effect=RuntimeError('sensitive synthetic text')):
            response = self.invite()
        self.assertEqual(response.status_code,503); self.assertNotIn('sensitive',response.text)
        self.assert_no_new_records()

    def test_commit_acknowledgement_and_refresh_are_unknown(self):
        self.schema(); self.enable()
        original_commit = Session.commit
        def committed_then_error(db): original_commit(db); raise RuntimeError('synthetic')
        with patch.object(Session, 'commit', committed_then_error): response = self.invite()
        self.assertEqual(response.status_code, 503); self.assertIn('could not be confirmed',response.json()['detail'])
        self.assertEqual(len(self.rows('review_assignments')),1)
        self.assertEqual(len(self.rows('email_outbox')),1)
        with patch.object(Session, 'refresh', side_effect=RuntimeError('synthetic')):
            response = self.invite('Other')
        self.assertEqual(response.status_code,503); self.assertIn('could not be confirmed',response.json()['detail'])
        self.assertEqual(len(self.rows('review_assignments')),2)
        self.assertEqual(len(self.rows('email_outbox')),2)

    def test_validation_decline_replacement_and_later_round(self):
        self.schema(); self.enable()
        self.assertEqual(self.invite(round_no=2).status_code,409)
        self.assertEqual(self.invite('Author').status_code,400)
        first=self.invite(); self.assertEqual(first.status_code,201); aid=first.json()['assignment_id']
        self.assertEqual(self.invite().status_code,409)
        self.actor=SimpleNamespace(user_id=self.ids['Reviewer'],role='reviewer',name='Reviewer')
        self.assertEqual(self.client.get(f'/assignments/{aid}/download').status_code,409)
        self.assertEqual(self.client.post(f'/assignments/{aid}/invitation-response',json={'decision':'decline'}).status_code,200)
        self.actor=SimpleNamespace(user_id=self.ids['Eic'],role='eic',name='Eic')
        self.assertEqual(self.invite('Other').status_code,201)
        self.assertEqual(len(self.rows('notification_events')),2)
        with self.Session() as db:
            m=db.get(models.Manuscript,self.mid); m.status='revised'; m.review_round=2
            path=self.root/'uploads'/'manuscripts'/str(self.mid)/'revision.doc'
            path.parent.mkdir(parents=True); path.write_bytes(bytes.fromhex('D0CF11E0A1B11AE1')+b'synthetic')
            db.add(models.ReviewRoundFile(manuscript_id=self.mid,review_round=2,source_kind='author_revision',confirmation_id=None,file_path=str(path.relative_to(self.root))))
            db.commit()
        later=self.invite('Reviewer',round_no=2); self.assertEqual(later.status_code,201,later.text)
        with self.Session() as db:
            binding=db.get(models.ReviewAssignmentFile,later.json()['assignment_id'])
            self.assertEqual(db.get(models.ReviewRoundFile,binding.round_file_id).review_round,2)
        self.assertEqual(len(self.rows('notification_events')),3)

    def test_sqlite_busy_uses_rollback_without_retry(self):
        self.schema(); self.enable()
        lock=sqlite3.connect(self.path,timeout=.1); lock.execute('BEGIN IMMEDIATE')
        try: response=self.invite()
        finally: lock.rollback(); lock.close()
        self.assertEqual(response.status_code,503); self.assertIn('contention',response.json()['detail'])
        self.assert_no_new_records()

    def test_sqlite_exclusive_lock_during_initial_read(self):
        self.schema(); self.enable()
        lock=sqlite3.connect(self.path,timeout=.1); lock.execute('BEGIN EXCLUSIVE')
        try: response=self.invite()
        finally: lock.rollback(); lock.close()
        self.assertEqual(response.status_code,503); self.assertIn('contention',response.json()['detail'])
        self.assert_no_new_records()

    def test_no_historical_backfill_or_existing_data_changes(self):
        self.schema(); self.enable()
        with self.Session() as db:
            old=models.ReviewAssignment(manuscript_id=self.mid,reviewer_id=self.ids['Other'],assigned_by=self.ids['Editor'],review_round=1,status='completed')
            db.add(old); db.flush(); old_id=old.assignment_id
            db.add(models.Notification(user_id=self.ids['Other'],manuscript_id=self.mid,notification_type='legacy',message='historical',is_read=False))
            db.commit()
        before={name:self.rows(name) for name in ('users','manuscripts','review_assignments','notifications')}
        pdf_hash=self.hash()
        response=self.invite(); self.assertEqual(response.status_code,201,response.text)
        for name, old_rows in before.items():
            for row in old_rows: self.assertIn(row,self.rows(name),name)
        with self.Session() as db:
            events=db.scalars(select(models.NotificationEvent)).all()
            self.assertEqual(len(events),1)
            self.assertNotEqual(events[0].invitation_assignment_id,old_id)
        self.assertEqual(self.hash(),pdf_hash)

    def test_rollback_failure_reports_unknown(self):
        self.schema(); self.enable()
        with patch.object(assignments,'enqueue_reviewer_invitation',side_effect=RuntimeError('synthetic')), patch.object(Session,'rollback',side_effect=RuntimeError('synthetic')):
            response=self.invite()
        self.assertEqual(response.status_code,503)
        self.assertIn('could not be confirmed',response.json()['detail'])
        self.assert_no_new_records()

    def test_concurrent_requests_remain_atomic(self):
        self.schema(); self.enable()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda name: self.invite(name), ('Reviewer','Other')))
        for result in results: self.assertIn(result.status_code,(201,409,503),result.text)
        count=len(self.rows('review_assignments'))
        self.assertGreaterEqual(count,1)
        for table in ('review_assignment_files','reviewer_invitations','notifications','notification_events','email_outbox'):
            self.assertEqual(len(self.rows(table)),count,table)
        self.assertEqual(len(self.rows('email_delivery_attempts')),0)

    def test_concurrent_same_reviewer_requests_remain_atomic(self):
        self.schema(); self.enable()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: self.invite(), range(2)))
        for result in results: self.assertIn(result.status_code,(201,409,503),result.text)
        count=len(self.rows('review_assignments'))
        self.assertGreaterEqual(count,1)
        for table in ('review_assignment_files','reviewer_invitations','notifications','notification_events','email_outbox'):
            self.assertEqual(len(self.rows(table)),count,table)


if __name__ == '__main__': unittest.main()
