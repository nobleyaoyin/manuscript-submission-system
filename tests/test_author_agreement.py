"""Author agreement actions and migration, exclusively against disposable data."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest, agreement_migration
from app import models
import app.main as main
import app.routers.eic as eic


class AuthorAgreementTest(ReviewerDownloadsTest):
    def setUp(self):
        super().setUp()
        self.chief = models.User(name='EIC', email='chief-agreement@example.com', password_hash='x', role='eic')
        self.other_author = models.User(name='Other author', email='other-author@example.com', password_hash='x', role='author')
        self.other_editor = models.User(name='Other editor', email='other-editor@example.com', password_hash='x', role='editor')
        self.db.add_all([self.chief, self.other_author, self.other_editor]); self.db.commit()

    def peer_decision(self, decision='accept'):
        response=self.assign(self.reviewer,1); self.assertEqual(response.status_code,201,response.text)
        aid=response.json()['assignment_id']; self.current=self.reviewer
        review=self.client.post('/reviews/',json={'assignment_id':aid,'comments':'Review complete','recommendation':'accept'})
        self.assertEqual(review.status_code,201,review.text);self.current=self.chief
        return self.client.post(f'/eic/manuscripts/{self.mid}/decision',json={'decision':decision,'comments':'Decision'})

    def agreement(self):
        self.current=self.author;items=self.client.get('/agreements/my').json()
        return next(item for item in items if item['manuscript_id']==self.mid)

    def final_submit(self):
        self.current=self.editor
        return self.client.post(f'/eic/final-editing/{self.mid}/submit',data={'comments':'Final edit'},files={'file':('final.pdf',b'%PDF-final','application/pdf')})

    def test_acceptance_permissions_version_and_final_editing(self):
        with patch.object(eic,'BASE_DIR',self.root):
            result=self.peer_decision();self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()['manuscript_status'],'awaiting_author_agreement')
            item=self.agreement(); aid=item['agreement_id'];version=item['agreement_version']
            self.assertEqual(item['status'],'pending');self.assertIn('Prototype placeholder',item['agreement_text'])
            self.assertEqual(self.final_submit().status_code,409)
            self.manuscript.status='final_editing';self.db.commit()
            self.assertEqual(self.final_submit().status_code,409)
            self.manuscript.status='awaiting_author_agreement';self.db.commit()
            self.current=self.other_author
            self.assertEqual(self.client.post(f'/agreements/{aid}/respond',json={'decision':'accept','agreement_version':version}).status_code,403)
            self.assertEqual(self.client.get(f'/agreements/manuscripts/{self.mid}').status_code,404)
            self.current=self.reviewer
            self.assertEqual(self.client.post(f'/agreements/{aid}/respond',json={'decision':'accept','agreement_version':version}).status_code,403)
            self.current=self.other_editor
            self.assertEqual(self.client.get(f'/agreements/manuscripts/{self.mid}').status_code,404)
            self.current=self.editor
            self.assertEqual(self.client.get(f'/agreements/manuscripts/{self.mid}').json()['status'],'pending')
            dashboard=self.client.get('/manuscripts/dashboard').json();self.assertEqual(next(x for x in dashboard if x['manuscript_id']==self.mid)['author_agreement_status'],'pending')
            self.current=self.chief
            self.assertEqual(self.client.get(f'/eic/manuscripts/{self.mid}/overview').json()['author_agreement']['status'],'pending')
            self.current=self.author
            self.assertEqual(self.client.post(f'/agreements/{aid}/respond',json={'decision':'accept','agreement_version':'stale-v0'}).status_code,409)
            accepted=self.client.post(f'/agreements/{aid}/respond',json={'decision':'accept','agreement_version':version})
            self.assertEqual(accepted.status_code,200,accepted.text);self.assertEqual(accepted.json()['manuscript_status'],'final_editing')
            self.assertIsNotNone(accepted.json()['responded_at'])
            self.assertEqual(self.client.post(f'/agreements/{aid}/respond',json={'decision':'accept','agreement_version':version}).status_code,409)
            self.current=self.chief
            invitation=self.client.post('/final-workflow/invite',json={'manuscript_id':self.mid,'editor_id':self.editor.user_id})
            self.assertEqual(invitation.status_code,201,invitation.text)
            self.current=self.editor
            self.assertEqual(self.client.post(f"/final-workflow/invitations/{invitation.json()['invitation_id']}/respond",json={'decision':'accept'}).status_code,200)
            result=self.final_submit();self.assertEqual(result.status_code,201,result.text)
            saved=self.db.get(models.AuthorAgreement,aid);self.db.refresh(saved)
            self.assertEqual(saved.author_id,self.author.user_id);self.assertEqual(saved.manuscript_id,self.mid);self.assertEqual(saved.agreement_version,version);self.assertEqual(saved.status,'accepted');self.assertIsNotNone(saved.responded_at)

    def test_decline_holds_final_editing_and_cannot_respond_twice(self):
        self.assertEqual(self.peer_decision().status_code,201)
        item=self.agreement(); payload={'decision':'decline','agreement_version':item['agreement_version']}
        result=self.client.post(f"/agreements/{item['agreement_id']}/respond",json=payload)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['manuscript_status'],'author_agreement_declined')
        self.assertEqual(self.final_submit().status_code,409)
        self.current=self.author
        self.assertEqual(self.client.post(f"/agreements/{item['agreement_id']}/respond",json=payload).status_code,409)
        self.current=self.chief
        self.assertEqual(self.client.get(f'/eic/manuscripts/{self.mid}/overview').json()['author_agreement']['status'],'declined')

    def test_revision_does_not_create_agreement(self):
        result=self.peer_decision('revision_required');self.assertEqual(result.status_code,201,result.text)
        self.current=self.author;self.assertEqual(self.client.get('/agreements/my').json(),[])

    def test_rejection_does_not_create_agreement(self):
        result=self.peer_decision('reject');self.assertEqual(result.status_code,201,result.text)
        self.current=self.author;self.assertEqual(self.client.get('/agreements/my').json(),[])

    def test_legacy_final_editing_without_record_is_preserved(self):
        with patch.object(eic,'BASE_DIR',self.root):
            assignment=models.ReviewAssignment(manuscript_id=self.mid,reviewer_id=self.reviewer.user_id,assigned_by=self.editor.user_id,review_round=1,status='completed')
            self.manuscript.status='final_editing';self.db.add(assignment);self.db.commit()
            result=self.final_submit();self.assertEqual(result.status_code,201,result.text)

    def test_migration_preserves_history_and_is_idempotent(self):
        self.manuscript.status='final_editing'
        self.db.add(models.EICDecision(manuscript_id=self.mid,eic_id=self.chief.user_id,review_round=1,decision='accept',comments='Historical decision'))
        self.db.add(models.FinalEditingSubmission(manuscript_id=self.mid,editor_id=self.editor.user_id,file_path='uploads/final_editing/historical.pdf',comments='Historical edit',status='pending_author'))
        self.db.commit()
        path=self.root/'before004.sqlite'
        with sqlite3.connect(self.db_path) as source,sqlite3.connect(path) as dest:source.backup(dest)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE author_agreements');db.execute("DELETE FROM schema_migrations WHERE migration_id='004_author_agreements'");db.commit()
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'")]
            before={t:db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
        agreement_migration.migrate(path);agreement_migration.migrate(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(before,{t:db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables})
            self.assertEqual(db.execute('SELECT count(*) FROM author_agreements').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_startup_refuses_missing_004(self):
        path=self.root/'unmigrated004.sqlite'
        with sqlite3.connect(self.db_path) as source,sqlite3.connect(path) as dest:source.backup(dest)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE author_agreements');db.execute("DELETE FROM schema_migrations WHERE migration_id='004_author_agreements'");db.commit()
        engine=create_engine(f'sqlite:///{path}',connect_args={'check_same_thread':False})
        try:
            with patch.object(main,'engine',engine):
                with self.assertRaisesRegex(RuntimeError,'004_author_agreements'):
                    with TestClient(main.app):pass
            with sqlite3.connect(path) as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='author_agreements'").fetchone())
        finally:engine.dispose()

if __name__=='__main__':unittest.main()
