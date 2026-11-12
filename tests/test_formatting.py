"""Formatter workflow tests with disposable database and uploads."""
import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
import app.main as main
from pathlib import Path
from test_final_workflow import FinalWorkflowTest
from test_reviewer_download import formatter_migration
from app import models
import app.routers.eic as eic
import app.routers.formatting as formatting

class FormattingTest(FinalWorkflowTest):
    def setUp(self):
        super().setUp()
        self.formatter=models.User(name='Formatter',email='formatter@example.com',password_hash='x',role='formatter')
        self.other_formatter=models.User(name='Other formatter',email='formatter2@example.com',password_hash='x',role='formatter')
        self.db.add_all([self.formatter,self.other_formatter]);self.db.commit()
    def prepare_final(self):
        item=self.reach_agreement();self.current=self.author
        self.assertEqual(self.client.post(f"/agreements/{item['agreement_id']}/respond",json={'decision':'accept','agreement_version':item['agreement_version']}).status_code,200)
        inv=self.invite(self.editor).json()['invitation_id'];self.assertEqual(self.respond(self.editor,inv,'accept').status_code,200)
        res=self.upload(self.editor,b'%PDF-approved-final');self.assertEqual(res.status_code,201,res.text);sid=res.json()['submission_id']
        self.assertEqual(self.review(self.chief,sid,'eic','approve').status_code,200)
        self.assertEqual(self.review(self.author,sid,'author','approve').status_code,200)
    def f_invite(self,who):
        self.current=self.chief;return self.client.post('/formatting/invite',json={'manuscript_id':self.mid,'formatter_id':who.user_id})
    def f_action(self,who,path,data):
        self.current=who;return self.client.post(path,json=data)
    def test_formatting_and_publication(self):
        with patch.object(eic,'BASE_DIR',self.root),patch.object(formatting,'BASE_DIR',self.root):
            self.assertEqual(self.f_invite(self.formatter).status_code,409)
            self.prepare_final()
            first=self.f_invite(self.other_formatter);self.assertEqual(first.status_code,201,first.text);iid=first.json()['invitation_id']
            self.current=self.other_formatter;self.assertEqual(self.client.get(f'/formatting/invitations/{iid}/input').status_code,403)
            self.assertEqual(self.f_action(self.formatter,f'/formatting/invitations/{iid}/respond',{'decision':'accept'}).status_code,403)
            self.assertEqual(self.f_action(self.other_formatter,f'/formatting/invitations/{iid}/respond',{'decision':'decline'}).status_code,200)
            second=self.f_invite(self.formatter);self.assertEqual(second.status_code,201,second.text);iid=second.json()['invitation_id']
            self.assertEqual(self.f_action(self.formatter,f'/formatting/invitations/{iid}/respond',{'decision':'accept'}).status_code,200)
            self.current=self.other_formatter;self.assertEqual(self.client.get(f'/formatting/invitations/{iid}/input').status_code,403)
            self.current=self.formatter;self.assertEqual(self.client.get(f'/formatting/invitations/{iid}/input').content,b'%PDF-approved-final')
            self.assertEqual(self.client.get(f'/manuscripts/{self.mid}/download').status_code,403)
            def upload(content):
                self.current=self.formatter;return self.client.post(f'/formatting/invitations/{iid}/submit',data={'comments':'Formatted'},files={'file':('formatted.pdf',content,'application/pdf')})
            one=upload(b'%PDF-layout-one');self.assertEqual(one.status_code,201,one.text);sid=one.json()['submission_id']
            self.current=self.other_formatter;self.assertEqual(self.client.get(f'/formatting/submissions/{sid}/download').status_code,403)
            self.current=self.reviewer;self.assertEqual(self.client.get(f'/formatting/submissions/{sid}/download').status_code,403)
            self.assertEqual(upload(b'%PDF-duplicate').status_code,409)
            self.assertEqual(self.f_action(self.chief,f'/formatting/submissions/{sid}/publish',{}).status_code,409)
            self.assertEqual(self.f_action(self.other_author,f'/formatting/submissions/{sid}/author-review',{'decision':'approve','comments':'ok'}).status_code,403)
            self.assertEqual(self.f_action(self.author,f'/formatting/submissions/{sid}/author-review',{'decision':'request_revision','comments':'fix'}).json()['status'],'author_revision_requested')
            two=upload(b'%PDF-layout-two');self.assertEqual(two.status_code,201,two.text);sid2=two.json()['submission_id']
            self.assertEqual(self.f_action(self.author,f'/formatting/submissions/{sid2}/author-review',{'decision':'approve','comments':'ok'}).status_code,200)
            self.assertEqual(self.f_action(self.chief,f'/formatting/submissions/{sid2}/eic-review',{'decision':'request_revision','comments':'fix'}).status_code,200)
            three=upload(b'%PDF-layout-three');self.assertEqual(three.status_code,201,three.text);sid3=three.json()['submission_id']
            self.assertEqual(self.f_action(self.author,f'/formatting/submissions/{sid3}/author-review',{'decision':'approve','comments':'ok'}).status_code,200)
            self.assertEqual(self.f_action(self.chief,f'/formatting/submissions/{sid3}/eic-review',{'decision':'approve','comments':'ok'}).status_code,200)
            self.assertEqual(self.f_action(self.author,f'/formatting/submissions/{sid3}/publish',{}).status_code,403)
            self.db.expire_all(); stored=self.db.get(models.FormattingSubmission,sid3); path=self.root/stored.file_path
            path.write_bytes(b'%PDF-tampered');self.assertEqual(self.f_action(self.chief,f'/formatting/submissions/{sid3}/publish',{}).status_code,409)
            path.write_bytes(b'%PDF-layout-three')
            pub=self.f_action(self.chief,f'/formatting/submissions/{sid3}/publish',{});self.assertEqual(pub.status_code,201,pub.text);self.assertEqual(pub.json()['doi_status'],'pending_external')
            self.assertEqual(self.f_action(self.chief,f'/formatting/submissions/{sid3}/publish',{}).status_code,409)
            self.current=self.author;self.assertEqual(self.client.get(f'/formatting/submissions/{sid3}/download').content,b'%PDF-layout-three')
            self.assertEqual((self.root/self.pdf).read_bytes(),b'%PDF-first')
    def test_006_migration_preserves_tables(self):
        path=self.root/'before006.sqlite'
        with sqlite3.connect(self.db_path) as a,sqlite3.connect(path) as b:a.backup(b)
        with sqlite3.connect(path) as c:
            c.execute('DROP TABLE prototype_publications');c.execute('DROP TABLE formatting_submissions');c.execute('DROP TABLE formatter_invitations');c.execute("DELETE FROM schema_migrations WHERE migration_id='006_formatter_publication'");c.commit()
            names=[x[0] for x in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' and name!='schema_migrations'")]
            before={n:c.execute(f'SELECT * FROM "{n}"').fetchall() for n in names}
        formatter_migration.migrate(path);formatter_migration.migrate(path)
        with sqlite3.connect(path) as c:
            self.assertEqual(before,{n:c.execute(f'SELECT * FROM "{n}"').fetchall() for n in names});self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok');self.assertFalse(c.execute('pragma foreign_key_check').fetchall())

    def test_startup_refuses_missing_006(self):
        path=self.root/'unmigrated006.sqlite'
        with sqlite3.connect(self.db_path) as a,sqlite3.connect(path) as b:a.backup(b)
        with sqlite3.connect(path) as c:
            c.execute('DROP TABLE prototype_publications');c.execute('DROP TABLE formatting_submissions');c.execute('DROP TABLE formatter_invitations');c.execute("DELETE FROM schema_migrations WHERE migration_id='006_formatter_publication'");c.commit()
        engine=create_engine(f'sqlite:///{path}',connect_args={'check_same_thread':False})
        try:
            with patch.object(main,'engine',engine):
                with self.assertRaisesRegex(RuntimeError,'006_formatter_publication'):
                    with TestClient(main.app):pass
        finally:engine.dispose()
