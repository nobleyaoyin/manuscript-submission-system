
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notification, User
from app.routers.users import get_current_user


router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"]
)


def notification_to_dict(notification: Notification):
    return {
        "notification_id": notification.notification_id,
        "user_id": notification.user_id,
        "manuscript_id": notification.manuscript_id,
        "notification_type": notification.notification_type,
        "message": notification.message,
        "is_read": notification.is_read,
        "created_at": notification.created_at,
    }


# Get notifications for the currently logged-in user
@router.get("/my")
def get_my_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notifications = db.scalars(
        select(Notification)
        .where(Notification.user_id == current_user.user_id)
        .order_by(Notification.created_at.desc())
    ).all()

    return [notification_to_dict(item) for item in notifications]


# Count unread notifications
@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = db.scalar(
        select(func.count(Notification.notification_id))
        .where(
            Notification.user_id == current_user.user_id,
            Notification.is_read.is_(False),
        )
    )

    return {"unread_count": count or 0}


# Mark one notification as read
@router.patch("/{notification_id}/read")
def mark_notification_as_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = db.get(Notification, notification_id)

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    if notification.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read your own notifications",
        )

    notification.is_read = True
    db.commit()
    db.refresh(notification)

    return notification_to_dict(notification)
