"""Invitation compatibility: assignments without an invitation predate this feature."""
from sqlalchemy import select
from app.models import ReviewerInvitation, ReviewAssignment


def invitation_status(db, assignment):
    invitation = db.get(ReviewerInvitation, assignment.assignment_id)
    return invitation.status if invitation else 'legacy'


def may_review(db, assignment):
    return invitation_status(db, assignment) in ('accepted', 'legacy')


def not_declined():
    return ~ReviewAssignment.assignment_id.in_(select(ReviewerInvitation.assignment_id).where(ReviewerInvitation.status == 'declined'))
