"""Final editor workflow against disposable SQLite and upload directories."""
import sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest,final_migration
from app import models
import app.main as main
import app.routers.eic as eic

class FinalWorkflowTest(ReviewerDownloadsTest):
    def setUp(self):
        super().setUp()
        self.chief=models.User(name='EIC',email='final-chief@example.com',password_hash='x',role='eic')
        self.alternate=models.User(name='Other editor',email='final-other@example.com',password_hash='x',role='editor')
        self.other_author=models.User(name='Other author',email='final-author@example.com',password_hash='x',role='author')
        self.db.add_all([self.chief,self.alternate,self.other_author]);self.db.commit()
    def reach_agreement(self):
        a=self.assign(self.reviewer,1);self.assertEqual(a.status_code,201,a.text)
        self.current=self.reviewer;response=self.client.post('/reviews/',json={'assignment_id':a.json()['assignment_id'],'comments':'Review','recommendation':'accept'});self.assertEqual(response.status_code,201,response.text)
        self.current=self.chief;response=self.client.post(f'/eic/manuscripts/{self.mid}/decision',json={'decision':'accept','comments':'Accepted'});self.assertEqual(response.status_code,201,response.text)
        self.current=self.author;return self.client.get('/agreements/my').json()[0]
    def invite(self,who):
        self.current=self.chief;return self.client.post('/final-workflow/invite',json={'manuscript_id':self.mid,'editor_id':who.user_id})
    def respond(self,who,inv,decision):
        self.current=who;return self.client.post(f'/final-workflow/invitations/{inv}/respond',json={'decision':decision})
    def upload(self,who,content):
        self.current=who;return self.client.post(f'/eic/final-editing/{self.mid}/submit',data={'comments':'Edited'},files={'file':('final.pdf',content,'application/pdf')})
    def review(self,who,sid,role,decision):
        self.current=who;return self.client.post(f'/final-workflow/submissions/{sid}/{role}-review',json={'decision':decision,'comments':'Reviewed'})
    def test_invitation_replacement_corrections_and_confirmation(self):
        with patch.object(eic,'BASE_DIR',self.root):
            item=self.reach_agreement();self.assertEqual(self.invite(self.alternate).status_code,409)
            self.current=self.author;resp=self.client.post(f"/agreements/{item['agreement_id']}/respond",json={'decision':'accept','agreement_version':item['agreement_version']});self.assertEqual(resp.status_code,200,resp.text)
            self.assertIsNone(self.client.get('/final-workflow/my').json()[0]['final_invitation_status'])
            first=self.invite(self.alternate);self.assertEqual(first.status_code,201,first.text);first_id=first.json()['invitation_id']
            self.current=self.author;self.assertEqual(self.client.get('/final-workflow/my').json()[0]['final_invitation_status'],'pending')
            self.assertEqual(self.invite(self.editor).status_code,409)
            self.assertEqual(self.respond(self.editor,first_id,'accept').status_code,403)
            self.assertEqual(self.respond(self.alternate,first_id,'decline').status_code,200)
            self.assertEqual(self.respond(self.alternate,first_id,'accept').status_code,409)
            second=self.invite(self.editor);self.assertEqual(second.status_code,201,second.text);iid=second.json()['invitation_id']
            self.assertEqual(self.upload(self.editor,b'%PDF-before-accept').status_code,403)
            self.assertEqual(self.respond(self.editor,iid,'accept').status_code,200)
            self.assertEqual(self.upload(self.alternate,b'%PDF-unauthorized').status_code,403)
            one=self.upload(self.editor,b'%PDF-final-one');self.assertEqual(one.status_code,201,one.text);s1=one.json()['submission_id'];self.assertEqual(one.json()['status'],'pending_eic')
            self.assertEqual(self.upload(self.editor,b'%PDF-duplicate').status_code,409)
            self.assertEqual(self.review(self.author,s1,'author','approve').status_code,409)
            self.assertEqual(self.review(self.reviewer,s1,'eic','approve').status_code,403)
            self.assertEqual(self.review(self.chief,s1,'eic','request_revision').json()['status'],'eic_revision_requested')
            two=self.upload(self.editor,b'%PDF-final-two');self.assertEqual(two.status_code,201,two.text);s2=two.json()['submission_id']
            self.assertEqual(self.review(self.chief,s1,'eic','approve').status_code,409)
            self.assertEqual(self.review(self.chief,s2,'eic','approve').json()['status'],'pending_author')
            self.assertEqual(self.review(self.other_author,s2,'author','approve').status_code,403)
            self.assertEqual(self.review(self.author,s2,'author','request_revision').json()['status'],'author_revision_requested')
            three=self.upload(self.editor,b'%PDF-final-three');self.assertEqual(three.status_code,201,three.text);s3=three.json()['submission_id']
            self.assertEqual(self.review(self.chief,s3,'eic','approve').status_code,200)
            self.assertEqual(self.review(self.author,s3,'author','approve').json()['status'],'author_approved')
            self.assertEqual(self.review(self.author,s3,'author','approve').status_code,409)
            self.assertEqual(self.upload(self.editor,b'%PDF-after-approval').status_code,409)
            self.current=self.author
            self.assertEqual(self.client.get(f'/eic/final-editing/submissions/{s1}/download').content,b'%PDF-final-one')
            self.assertEqual(self.client.get(f'/eic/final-editing/submissions/{s2}/download').content,b'%PDF-final-two')
            self.assertEqual(self.client.get(f'/eic/final-editing/submissions/{s3}/download').content,b'%PDF-final-three')
            self.assertEqual((self.root/self.pdf).read_bytes(),b'%PDF-first')
            self.db.refresh(self.manuscript);self.assertEqual(self.manuscript.status,'final_editing')
            self.current=self.chief;overview=self.client.get(f'/eic/manuscripts/{self.mid}/overview').json()
            self.assertEqual([i['status'] for i in overview['final_invitations']],['declined','accepted'])
            self.assertEqual(overview['final_workflow_submissions'][0]['status'],'author_approved')
    def test_migration_preserves_existing_tables(self):
        path=self.root/'before005.sqlite'
        with sqlite3.connect(self.db_path) as a,sqlite3.connect(path) as b:a.backup(b)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE final_editing_submission_invitations');db.execute('DROP TABLE final_editor_invitations');db.execute("DELETE FROM schema_migrations WHERE migration_id='005_final_editor_workflow'");db.commit()
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'")]
            before={t:db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
        final_migration.migrate(path);final_migration.migrate(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(before,{t:db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables})
            self.assertEqual(db.execute('SELECT count(*) FROM final_editor_invitations').fetchone()[0],0);self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_startup_refuses_missing_migration(self):
        path=self.root/'before005.sqlite'
        with sqlite3.connect(self.db_path) as a,sqlite3.connect(path) as b:a.backup(b)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE final_editing_submission_invitations');db.execute('DROP TABLE final_editor_invitations');db.execute("DELETE FROM schema_migrations WHERE migration_id='005_final_editor_workflow'");db.commit()
        engine=create_engine(f'sqlite:///{path}',connect_args={'check_same_thread':False})
        try:
            with patch.object(main,'engine',engine):
                with self.assertRaisesRegex(RuntimeError,'005_final_editor_workflow'):
                    with TestClient(main.app):pass
        finally:engine.dispose()

if __name__=='__main__':unittest.main()
