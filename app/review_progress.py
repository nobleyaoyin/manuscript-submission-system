"""Prospective per-manuscript review policy and round-specific decision progress."""
from sqlalchemy import select
from app.models import (
    ManuscriptReviewPolicy, Review, ReviewAssignment, ReviewAssignmentFile,
    ReviewerInvitation, ReviewRoundFile,
)
from app.reviewer_invitations import not_declined


def review_progress(db, manuscript, review_round=None):
    review_round = manuscript.review_round if review_round is None else review_round
    policy = db.get(ManuscriptReviewPolicy, manuscript.manuscript_id)
    required = policy.minimum_completed_reviewers if policy else 1
    active = db.scalars(select(ReviewAssignment).where(
        ReviewAssignment.manuscript_id == manuscript.manuscript_id,
        ReviewAssignment.review_round == review_round,
        not_declined(),
    )).all()
    completed = set()
    incomplete = 0
    for assignment in active:
        review = db.scalar(select(Review).where(Review.assignment_id == assignment.assignment_id))
        invitation = db.get(ReviewerInvitation, assignment.assignment_id)
        is_complete = assignment.status == 'completed' and review is not None
        if not is_complete:
            incomplete += 1
        if not is_complete:
            continue
        if policy:
            binding = db.get(ReviewAssignmentFile, assignment.assignment_id)
            round_file = db.get(ReviewRoundFile, binding.round_file_id) if binding else None
            if not (invitation and invitation.status == 'accepted' and round_file
                    and round_file.manuscript_id == manuscript.manuscript_id
                    and round_file.review_round == review_round):
                incomplete += 1
                continue
        completed.add(assignment.reviewer_id)
    count = len(completed)
    if count < required:
        reason = (f'At least {required} distinct accepted reviewers must complete reviews for this round ({count}/{required}).'
                  if policy else 'At least one assigned reviewer must complete a review for this round.')
    elif incomplete:
        reason = 'All other non-declined invitations and reviews must be completed before a decision.'
    else:
        reason = None
    return {'review_round': review_round, 'policy': 'two_reviewers' if policy else 'legacy',
            'required': required, 'completed_distinct': count, 'active_assignments': len(active),
            'incomplete_assignments': incomplete, 'can_decide': reason is None, 'blocked_reason': reason}
