"""EIC overview and workflow checks against an isolated temporary SQLite database."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest
from app import models


class EICDashboardTest(ReviewerDownloadsTest):
    def test_eic_overview_authorization_and_draft_privacy(self):
        eic = models.User(name="EIC", email="eic@example.com", password_hash="x", role="eic")
        draft = models.Manuscript(title="Private draft", abstract="Private", author_id=self.author.user_id, status="draft", review_round=1)
        self.db.add_all([eic, draft]); self.db.commit()
        self.current = self.reviewer
        self.assertEqual(self.client.get('/eic/editors').status_code, 403)
        self.assertEqual(self.client.get(f'/eic/manuscripts/{self.mid}/overview').status_code, 403)
        self.current = self.author
        self.assertEqual(self.client.get(f'/eic/manuscripts/{self.mid}/overview').status_code, 403)
        self.current = eic
        self.assertEqual(self.client.get(f'/eic/manuscripts/{draft.manuscript_id}/overview').status_code, 404)
        self.assertEqual(self.client.get('/eic/manuscripts/999999/overview').status_code, 404)
        self.assertEqual(self.client.get('/eic/editors').json()[0]['name'], 'Editor')
        assignment = self.assign(self.reviewer, 1)
        self.assertEqual(assignment.status_code, 201, assignment.text)
        self.current = self.reviewer
        response = self.client.post('/reviews/', json={'assignment_id': assignment.json()['assignment_id'], 'comments': 'Detailed review', 'recommendation': 'accept'})
        self.assertEqual(response.status_code, 201, response.text)
        self.current = eic
        overview = self.client.get(f'/eic/manuscripts/{self.mid}/overview').json()
        self.assertEqual(overview['assignments'][0]['reviewer'], 'Reviewer')
        self.assertEqual(overview['assignments'][0]['review']['comments'], 'Detailed review')
        response = self.client.post(f'/eic/manuscripts/{self.mid}/decision', json={'decision':'accept', 'comments':'Ready'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.get(f'/eic/manuscripts/{self.mid}/overview').json()['decisions'][0]['decision'], 'accept')

if __name__ == '__main__': unittest.main()
