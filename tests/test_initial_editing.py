"""Stage 3 authorization and ordering against temporary SQLite and uploads."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest
from app import models
import app.routers.eic as eic_router


class InitialEditingTest(ReviewerDownloadsTest):
    def test_invitation_approval_order_and_revision(self):
        with patch.object(eic_router, 'BASE_DIR', self.root):
            chief = models.User(name='EIC', email='chief@example.test', password_hash='x', role='eic')
            other_editor = models.User(name='Other editor', email='othereditor@example.test', password_hash='x', role='editor')
            other_author = models.User(name='Other author', email='otherauthor@example.test', password_hash='x', role='author')
            manuscript = models.Manuscript(title='Stage 3', abstract='Synthetic', author_id=self.author.user_id, status='initial_scan_passed', review_round=1)
            self.db.add_all([chief, other_editor, other_author, manuscript]); self.db.commit(); mid = manuscript.manuscript_id
            self.current = chief
            invitation = self.client.post('/eic/initial-editor/invite', json={'manuscript_id':mid,'editor_id':self.editor.user_id})
            self.assertEqual(invitation.status_code, 201, invitation.text); inv = invitation.json()['invitation_id']
            self.current = self.editor
            items = self.client.get('/eic/initial-editing/my').json(); item = next(x for x in items if x['manuscript_id'] == mid)
            self.assertEqual(item['invitation']['status'], 'pending'); self.assertEqual(item['submissions'], [])
            self.current = other_editor
            self.assertFalse(any(x['manuscript_id'] == mid for x in self.client.get('/eic/initial-editing/my').json()))
            self.assertEqual(self.client.post(f'/eic/initial-editor/invitations/{inv}/respond', json={'decision':'accept'}).status_code, 403)
            self.current = other_author
            self.assertFalse(any(x['manuscript_id'] == mid for x in self.client.get('/eic/initial-editing/my').json()))
            self.current = self.reviewer
            self.assertEqual(self.client.get('/eic/initial-editing/my').status_code, 403)
            self.current = self.editor
            self.assertEqual(self.client.post(f'/eic/initial-editor/invitations/{inv}/respond', json={'decision':'accept'}).status_code, 200)
            pdf = b'%PDF-1.4\nsynthetic editing file'
            response = self.client.post(f'/eic/initial-editing/{mid}/submit', data={'comments':'Editing complete'}, files={'file':('editing.pdf', pdf, 'application/pdf')})
            self.assertEqual(response.status_code, 201, response.text); sub = response.json()['submission_id']
            self.assertEqual(self.client.post(f'/eic/initial-editing/{mid}/submit', data={'comments':'duplicate'}, files={'file':('editing.pdf',pdf,'application/pdf')}).status_code, 409)
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/editor-approval', json={'decision':'approve','comments':'Early'}).status_code, 409)
            self.current = chief
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/eic-approval', json={'decision':'approve','comments':'Early'}).status_code, 409)
            self.current = other_author
            self.assertEqual(self.client.get(f'/eic/initial-editing/submissions/{sub}/download').status_code, 403)
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/author-review', json={'decision':'approve','comments':'No'}).status_code, 403)
            self.current = self.author
            self.assertEqual(self.client.get(f'/eic/initial-editing/submissions/{sub}/download').content, pdf)
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/pre-review-confirmation', json={'decision':'confirm','comments':'Early'}).status_code, 409)
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/author-review', json={'decision':'approve','comments':'Approved'}).status_code, 200)
            self.current = self.editor
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/editor-approval', json={'decision':'approve','comments':'Approved'}).status_code, 200)
            self.current = chief
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/eic-approval', json={'decision':'approve','comments':'Approved'}).status_code, 200)
            self.current = self.author
            self.assertEqual(self.client.post(f'/eic/initial-editing/submissions/{sub}/pre-review-confirmation', json={'decision':'request_revision','comments':'Change requested'}).status_code, 200)
            item = next(x for x in self.client.get('/eic/initial-editing/my').json() if x['manuscript_id'] == mid)
            self.assertEqual(item['submissions'][0]['confirmation'], 'request_revision')
            self.current = self.editor
            response = self.client.post(f'/eic/initial-editing/{mid}/submit', data={'comments':'Updated'}, files={'file':('next.pdf',b'%PDF-next','application/pdf')})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.client.get(f'/eic/initial-editing/submissions/{sub}/download').content, pdf)

    def test_decline_returns_to_scan_passed(self):
        chief = models.User(name='EIC', email='chief@example.test', password_hash='x', role='eic')
        manuscript = models.Manuscript(title='Decline', abstract='Synthetic', author_id=self.author.user_id, status='initial_scan_passed', review_round=1)
        self.db.add_all([chief, manuscript]); self.db.commit(); self.current = chief
        response = self.client.post('/eic/initial-editor/invite', json={'manuscript_id':manuscript.manuscript_id,'editor_id':self.editor.user_id})
        self.current = self.editor
        self.assertEqual(self.client.post(f"/eic/initial-editor/invitations/{response.json()['invitation_id']}/respond", json={'decision':'decline'}).status_code, 200)
        self.db.refresh(manuscript); self.assertEqual(manuscript.status, 'initial_scan_passed')
        self.assertFalse(any(x['manuscript_id'] == manuscript.manuscript_id for x in self.client.get('/eic/initial-editing/my').json()))

if __name__ == '__main__': unittest.main()
