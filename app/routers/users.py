
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, UserLogin
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"],
)

# Read Bearer token from Authorization header
bearer_scheme = HTTPBearer(auto_error=False)


# Normalize email consistently for registration and login
def normalize_email(email: str) -> str:
    return email.strip().lower()


# Get the currently authenticated user
def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> User:

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = decode_access_token(credentials.credentials)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.scalar(
        select(User).where(User.user_id == user_id)
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# Check whether the current user has an allowed role
def require_role(*allowed_roles: str):

    def check_role(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:

        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )

        return current_user

    return check_role


# Register a new user
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    email = normalize_email(str(user_data.email))

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    new_user = User(
        name=user_data.name,
        email=email,
        password_hash=hash_password(user_data.password),
        role="author",
    )

    db.add(new_user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    db.refresh(new_user)

    return new_user


# Login and generate a JWT access token
@router.post("/login")
def login_user(
    login_data: UserLogin,
    db: Session = Depends(get_db),
):
    email = normalize_email(str(login_data.email))

    user = db.scalar(
        select(User).where(User.email == email)
    )

    if user is None or not verify_password(
        login_data.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(user.user_id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


# Get the currently logged-in user's information
@router.get("/me", response_model=UserResponse)
def read_current_user(
    current_user: Annotated[
        User,
        Depends(get_current_user),
    ],
):
    return current_user


# Test endpoint: only editors can access
@router.get("/editor-only")
def editor_only(
    current_user: Annotated[
        User,
        Depends(require_role("editor")),
    ],
):
    return {
        "message": "Welcome, Editor!",
        "user_id": current_user.user_id,
        "role": current_user.role,
    }
