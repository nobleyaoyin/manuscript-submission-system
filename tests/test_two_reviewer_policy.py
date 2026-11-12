"""Prospective two-reviewer rule with isolated databases and upload roots."""
import io
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reviewer_download import ReviewerDownloadsTest, policy_migration
from app import models
import app.main as main


class TwoReviewerPolicyTest(ReviewerDownloadsTest):
    def setUp(self):
        super().setUp()
        self.chief = models.User(name='EIC', email='chief-policy@example.test', password_hash='x', role='eic')
        self.third = models.User(name='Third reviewer', email='third-policy@example.test', password_hash='x', role='reviewer')
        self.db.add_all([self.chief, self.third]); self.db.commit()

    def enable_new_submission(self):
        self.manuscript.status = 'draft'
        self.manuscript.file_path = self.write(f'uploads/manuscripts/{self.mid}/original.pdf', b'%PDF-original')
        self.db.commit(); self.current = self.author
        result = self.client.post(f'/manuscripts/{self.mid}/submit')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNotNone(self.db.get(models.ManuscriptReviewPolicy, self.mid))
        # Existing isolated Stage 3 fixture already has the confirmed PDF and editor.
        self.manuscript.status = 'ready_for_peer_review'; self.db.commit()

    def invite(self, reviewer):
        self.current = self.editor
        result = self.client.post('/assignments/', json={'manuscript_id':self.mid, 'reviewer_id':reviewer.user_id, 'review_round':self.manuscript.review_round})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['assignment_id']

    def respond(self, reviewer, aid, decision):
        self.current = reviewer
        result = self.client.post(f'/assignments/{aid}/invitation-response', json={'decision':decision})
        self.assertEqual(result.status_code, 200, result.text)

    def submit_review(self, reviewer, aid):
        self.current = reviewer
        result = self.client.post('/reviews/', json={'assignment_id':aid, 'comments':'Review complete', 'recommendation':'accept'})
        self.assertEqual(result.status_code, 201, result.text)

    def progress(self):
        self.current = self.chief
        result = self.client.get(f'/eic/manuscripts/{self.mid}/overview')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['review_progress']

    def decision(self, choice='accept'):
        self.current = self.chief
        return self.client.post(f'/eic/manuscripts/{self.mid}/decision', json={'decision':choice,'comments':'Decision'})

    def test_editor_cannot_bypass_eic_after_reviews_complete(self):
        # Set up a manuscript governed by the new EIC workflow.
        self.enable_new_submission()

        # Complete two reviewer assignments.
        first = self.invite(self.reviewer)
        self.respond(self.reviewer, first, "accept")
        self.submit_review(self.reviewer, first)

        second = self.invite(self.other)
        self.respond(self.other, second, "accept")
        self.submit_review(self.other, second)

        # Complete the first-round EIC decision.
        response = self.decision("revision_required")
        self.assertEqual(response.status_code, 201, response.text)

        # Upload a revised manuscript through the real author workflow.
        data = io.BytesIO()

        with ZipFile(data, "w") as archive:
            archive.writestr("[Content_Types].xml", "x")
            archive.writestr("word/document.xml", "x")

        self.current = self.author

        response = self.client.post(
            f"/manuscripts/{self.mid}/upload",
            files={
                "file": (
                    "revised.docx",
                    data.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post(
            f"/manuscripts/{self.mid}/resubmit",
            json={"title": "Revised manuscript"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        self.db.refresh(self.manuscript)
        self.assertEqual(self.manuscript.review_round, 2)
        self.assertEqual(self.manuscript.status, "revised")

        # The existing reviews belong to Round 1, so complete Round 2.
        third = self.invite(self.reviewer)
        self.respond(self.reviewer, third, "accept")
        self.submit_review(self.reviewer, third)

        fourth = self.invite(self.other)
        self.respond(self.other, fourth, "accept")
        self.submit_review(self.other, fourth)

        self.assertTrue(self.progress()["can_decide"])

        # None of the legacy editor decisions may bypass the EIC.
        def record_counts():
            return (
                self.db.query(models.Notification)
                .filter_by(manuscript_id=self.mid)
                .count(),
                self.db.query(models.EICDecision)
                .filter_by(manuscript_id=self.mid)
                .count(),
                self.db.query(models.AuthorAgreement)
                .filter_by(manuscript_id=self.mid)
                .count(),
            )

        counts_before = record_counts()
        for choice in ("accepted", "rejected", "revision_required"):
            with self.subTest(decision=choice):
                self.current = self.editor

                response = self.client.patch(
                    f"/manuscripts/{self.mid}/decision",
                    json={"decision": choice},
                )

                self.assertEqual(response.status_code, 409, response.text)
                self.db.refresh(self.manuscript)
                self.assertEqual(self.manuscript.status, "revised")
                self.assertEqual(record_counts(), counts_before)
        # The authorized EIC can still accept the manuscript.
        response = self.decision("accept")
        self.assertEqual(response.status_code, 201, response.text)

        self.db.refresh(self.manuscript)
        self.assertEqual(
            self.manuscript.status,
            "awaiting_author_agreement",
        )

        agreements = self.db.query(models.AuthorAgreement).filter_by(
            manuscript_id=self.mid
        ).all()

        self.assertEqual(len(agreements), 1)
        self.assertEqual(agreements[0].status, "pending")

    def test_replacement_distinct_reviews_and_each_round_binding(self):
        self.enable_new_submission()
        self.assertEqual(self.progress()['completed_distinct'], 0)
        self.assertEqual(self.decision().status_code, 409)
        a = self.invite(self.reviewer)
        self.assertEqual(self.decision().status_code, 409)
        self.respond(self.reviewer, a, 'accept'); self.submit_review(self.reviewer, a)
        self.assertEqual(self.progress()['completed_distinct'], 1)
        self.assertEqual(self.decision().status_code, 409)
        duplicate = self.client.post('/assignments/', json={'manuscript_id':self.mid,'reviewer_id':self.reviewer.user_id,'review_round':1})
        self.assertEqual(duplicate.status_code, 409)
        self.current = self.editor
        duplicate = self.client.post('/assignments/', json={'manuscript_id':self.mid,'reviewer_id':self.reviewer.user_id,'review_round':1})
        self.assertEqual(duplicate.status_code, 409)
        declined = self.invite(self.other); self.respond(self.other, declined, 'decline')
        self.assertEqual(self.progress()['completed_distinct'], 1)
        self.assertEqual(self.decision().status_code, 409)
        replacement = self.invite(self.third)
        self.respond(self.third, replacement, 'accept')
        self.assertEqual(self.progress()['completed_distinct'], 1)
        self.submit_review(self.third, replacement)
        self.assertEqual(self.progress()['completed_distinct'], 2)
        self.assertEqual(self.decision('revision_required').status_code, 201)
        # Formally upload and resubmit a distinct DOCX for the second review round.
        data=io.BytesIO()
        with ZipFile(data,'w') as z:
            z.writestr('[Content_Types].xml','x'); z.writestr('word/document.xml','x')
        revised=data.getvalue(); self.current=self.author
        result=self.client.post(f'/manuscripts/{self.mid}/upload',files={'file':('revised.docx',revised,'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
        self.assertEqual(result.status_code,200,result.text)
        result=self.client.post(f'/manuscripts/{self.mid}/resubmit',json={'title':'Revised title'})
        self.assertEqual(result.status_code,200,result.text); self.db.refresh(self.manuscript)
        self.assertEqual(self.manuscript.review_round,2)
        self.assertEqual(self.progress()['completed_distinct'],0)
        self.assertEqual(self.decision().status_code,409)
        round2a=self.invite(self.reviewer); self.respond(self.reviewer,round2a,'accept')
        self.current=self.reviewer
        self.assertEqual(self.client.get(f'/assignments/{round2a}/download').content,revised)
        self.assertEqual(self.client.get(f'/assignments/{a}/download').content,b'%PDF-first')
        self.submit_review(self.reviewer,round2a)
        self.assertEqual(self.progress()['completed_distinct'],1)
        self.assertEqual(self.decision().status_code,409)
        round2b=self.invite(self.third); self.respond(self.third,round2b,'accept'); self.submit_review(self.third,round2b)
        self.assertEqual(self.progress()['completed_distinct'],2)
        self.assertEqual(self.decision().status_code,201)

    def test_duplicate_records_do_not_satisfy_minimum(self):
        self.enable_new_submission()
        aid=self.invite(self.reviewer); self.respond(self.reviewer,aid,'accept'); self.submit_review(self.reviewer,aid)
        # Simulate historical/corrupt duplicate records in the disposable database.
        original=self.db.get(models.ReviewAssignment,aid)
        duplicate=models.ReviewAssignment(manuscript_id=self.mid,reviewer_id=self.reviewer.user_id,assigned_by=self.editor.user_id,review_round=1,status='completed')
        self.db.add(duplicate); self.db.flush()
        self.db.add(models.ReviewerInvitation(assignment_id=duplicate.assignment_id,status='accepted'))
        binding=self.db.get(models.ReviewAssignmentFile,aid)
        self.db.add(models.ReviewAssignmentFile(assignment_id=duplicate.assignment_id,round_file_id=binding.round_file_id))
        self.db.add(models.Review(assignment_id=duplicate.assignment_id,comments='Duplicate',recommendation='accept'))
        self.db.commit(); self.assertEqual(original.reviewer_id,duplicate.reviewer_id)
        self.assertEqual(self.progress()['completed_distinct'],1)
        self.assertEqual(self.decision().status_code,409)

    def test_pending_and_legacy_records_do_not_count_for_new_policy(self):
        self.enable_new_submission()
        first=self.invite(self.reviewer); self.respond(self.reviewer,first,'accept'); self.submit_review(self.reviewer,first)
        pending=self.invite(self.other)
        self.assertEqual(self.progress()['completed_distinct'],1)
        self.assertEqual(self.decision().status_code,409)
        # An old-style assignment without an acceptance record is never counted for new submissions.
        self.db.delete(self.db.get(models.ReviewerInvitation,pending))
        assignment=self.db.get(models.ReviewAssignment,pending); assignment.status='completed'
        self.db.add(models.Review(assignment_id=pending,comments='Legacy',recommendation='accept')); self.db.commit()
        self.assertEqual(self.progress()['completed_distinct'],1)
        self.assertEqual(self.decision().status_code,409)

    def test_legacy_manuscript_keeps_existing_single_review_rule(self):
        self.assertIsNone(self.db.get(models.ManuscriptReviewPolicy,self.mid))
        aid=self.invite(self.reviewer); self.respond(self.reviewer,aid,'accept'); self.submit_review(self.reviewer,aid)
        self.assertEqual(self.progress()['policy'],'legacy')
        self.assertEqual(self.decision().status_code,201)

    def test_no_maximum_and_older_editor_endpoint_cannot_bypass(self):
        self.enable_new_submission()
        first=self.invite(self.reviewer); self.respond(self.reviewer,first,'accept'); self.submit_review(self.reviewer,first)
        self.manuscript.status='revised'; self.db.commit(); self.current=self.editor
        result=self.client.patch(f'/manuscripts/{self.mid}/decision',json={'decision':'accepted'})
        self.assertEqual(result.status_code,409)
        self.manuscript.status='ready_for_peer_review'; self.db.commit()
        second=self.invite(self.other); self.respond(self.other,second,'accept'); self.submit_review(self.other,second)
        third=self.invite(self.third)
        self.assertEqual(self.progress()['completed_distinct'],2)
        self.assertEqual(self.decision().status_code,409)  # Existing rule waits for other active reviewers.
        self.respond(self.third,third,'accept'); self.submit_review(self.third,third)
        self.assertEqual(self.progress()['completed_distinct'],3)
        self.assertEqual(self.decision().status_code,201)

    def test_startup_refuses_missing_003_without_creating_it(self):
        path=self.root/'unmigrated003.sqlite'
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(path) as dest: source.backup(dest)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE manuscript_review_policies')
            db.execute("DELETE FROM schema_migrations WHERE migration_id='003_two_reviewer_policy'"); db.commit()
        engine=create_engine(f'sqlite:///{path}',connect_args={'check_same_thread':False})
        try:
            with patch.object(main,'engine',engine):
                with self.assertRaisesRegex(RuntimeError,'003_two_reviewer_policy'):
                    with TestClient(main.app): pass
            with sqlite3.connect(path) as db:
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='manuscript_review_policies'").fetchone())
        finally:
            engine.dispose()

    def test_migration_preserves_preexisting_records(self):
        path=self.root/'before003.sqlite'
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(path) as dest: source.backup(dest)
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE manuscript_review_policies')
            db.execute("DELETE FROM schema_migrations WHERE migration_id='003_two_reviewer_policy'"); db.commit()
            tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'")]
            before={table:db.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
        policy_migration.migrate(path);policy_migration.migrate(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(before,{table:db.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables})
            self.assertEqual(db.execute('SELECT count(*) FROM manuscript_review_policies').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_legacy_editor_decision_still_works(self):
        # A genuinely legacy manuscript has no EIC workflow evidence.
        legacy = models.Manuscript(
            title="Legacy compatibility test",
            abstract="Legacy manuscript",
            author_id=self.author.user_id,
            status="submitted",
            review_round=1,
        )
        self.db.add(legacy)
        self.db.flush()
        legacy_id = legacy.manuscript_id

        assignment = models.ReviewAssignment(
            manuscript_id=legacy_id,
            reviewer_id=self.reviewer.user_id,
            assigned_by=self.editor.user_id,
            review_round=1,
            status="completed",
        )
        self.db.add(assignment)
        self.db.flush()
        self.db.add(models.Review(
            assignment_id=assignment.assignment_id,
            comments="The manuscript is ready for acceptance.",
            recommendation="accept",
        ))
        self.db.commit()

        from app.routers.manuscripts import uses_eic_workflow
        self.assertFalse(uses_eic_workflow(self.db, legacy_id))

        self.current = self.editor
        response = self.client.patch(
            f"/manuscripts/{legacy_id}/decision",
            json={"decision": "accepted"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(legacy)
        self.assertEqual(legacy.status, "accepted")

        notifications = self.db.query(models.Notification).filter_by(
            manuscript_id=legacy_id,
            notification_type="editor_decision",
        ).all()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].user_id, self.author.user_id)
        self.assertEqual(
            self.db.query(models.EICDecision)
            .filter_by(manuscript_id=legacy_id).count(), 0,
        )
        self.assertEqual(
            self.db.query(models.AuthorAgreement)
            .filter_by(manuscript_id=legacy_id).count(), 0,
        )

if __name__=='__main__': unittest.main()
