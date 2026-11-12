"""Reviewer invitations and migration tests, using the temporary reviewer fixtures."""
import sys
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest, invitation_migration
from app import models


class ReviewerInvitationTest(ReviewerDownloadsTest):
    def invite(self, who, role=None):
        self.current = role or self.editor
        return self.client.post('/assignments/', json={'manuscript_id':self.mid,'reviewer_id':who.user_id,'review_round':self.manuscript.review_round})

    def test_invitation_decline_replacement_and_permissions(self):
        self.current = self.author
        denied = self.client.post('/assignments/', json={'manuscript_id':self.mid,'reviewer_id':self.reviewer.user_id,'review_round':1})
        self.assertEqual(denied.status_code, 403)
        unrelated = models.User(name='Unassigned editor', email='unassigned@example.test', password_hash='x', role='editor')
        chief = models.User(name='EIC', email='chief@example.test', password_hash='x', role='eic')
        self.db.add_all([unrelated, chief]); self.db.commit()
        self.assertEqual(self.invite(self.reviewer, unrelated).status_code, 403)
        response = self.invite(self.reviewer, chief)
        self.assertEqual(response.status_code, 201, response.text); aid = response.json()['assignment_id']
        self.assertEqual(response.json()['invitation_status'], 'pending')
        self.current = self.reviewer
        self.assertEqual(self.client.get(f'/assignments/{aid}/download').status_code, 409)
        self.assertIn(self.client.get(f'/manuscripts/{self.mid}').status_code, (403, 404))
        self.assertEqual(self.client.post('/reviews/', json={'assignment_id':aid,'comments':'No','recommendation':'accept'}).status_code, 403)
        self.current = self.other
        self.assertEqual(self.client.post(f'/assignments/{aid}/invitation-response', json={'decision':'accept'}).status_code, 403)
        self.current = self.reviewer
        self.assertEqual(self.client.post(f'/assignments/{aid}/invitation-response', json={'decision':'decline'}).status_code, 200)
        self.assertEqual(self.client.post(f'/assignments/{aid}/invitation-response', json={'decision':'accept'}).status_code, 409)
        self.assertEqual(self.client.get(f'/assignments/{aid}/download').status_code, 409)
        self.current = chief
        self.assertEqual(self.client.post(f'/eic/manuscripts/{self.mid}/decision', json={'decision':'accept','comments':'No reviewer'}).status_code, 409)
        replacement = self.invite(self.other)
        self.assertEqual(replacement.status_code, 201, replacement.text); bid = replacement.json()['assignment_id']
        self.current = chief
        self.assertEqual(self.client.post(f'/eic/manuscripts/{self.mid}/decision', json={'decision':'accept','comments':'Pending'}).status_code, 409)
        self.current = self.other
        self.assertEqual(self.client.post(f'/assignments/{bid}/invitation-response', json={'decision':'accept'}).status_code, 200)
        self.assertEqual(self.client.get(f'/assignments/{bid}/download').content, b'%PDF-first')
        self.assertEqual(self.client.post('/reviews/', json={'assignment_id':bid,'comments':'Done','recommendation':'accept'}).status_code, 201)
        self.current = chief
        response = self.client.post(f'/eic/manuscripts/{self.mid}/decision', json={'decision':'accept','comments':'Reviewed'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.db.get(models.ReviewerInvitation, aid).status, 'declined')

    def test_legacy_assignment_remains_accessible(self):
        result = self.assign(self.reviewer, 1); aid = result.json()['assignment_id']
        self.db.delete(self.db.get(models.ReviewerInvitation, aid)); self.db.commit()
        self.current = self.reviewer
        self.assertEqual(self.client.get(f'/assignments/{aid}/download').content, b'%PDF-first')
        item = next(x for x in self.client.get('/assignments/my').json() if x['assignment_id'] == aid)
        self.assertEqual(item['invitation_status'], 'legacy')

    def test_migration_preserves_records_and_is_idempotent(self):
        path = self.root / 'snapshot.sqlite'
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(path) as dest:
            source.backup(dest)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE reviewer_invitations')
            db.execute("DELETE FROM schema_migrations WHERE migration_id='002_reviewer_invitations'")
            db.commit()
            before = {table: db.execute(f'SELECT * FROM "{table}"').fetchall() for table in ('users','manuscripts','review_assignments','reviews','review_round_files','review_assignment_files')}
        invitation_migration.migrate(path); invitation_migration.migrate(path)
        with sqlite3.connect(path) as db:
            after = {table: db.execute(f'SELECT * FROM "{table}"').fetchall() for table in before}
            self.assertEqual(before, after)
            self.assertEqual(db.execute('SELECT count(*) FROM reviewer_invitations').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

if __name__ == '__main__': unittest.main()
