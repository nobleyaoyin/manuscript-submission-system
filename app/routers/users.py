
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Request
from collections import deque
from threading import Lock
from time import monotonic
from app.config import IS_DEMO
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.email.registration_verification import (
    prepare_registration_verification,
    deliver_registration_verification,
)
from app.models import User
from app.email.verification_service import is_email_verified
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

# Single-process limiter: do not trust client-supplied forwarding headers.
# The global cap also bounds memory and protects the local demo behind a tunnel.
_login_attempts = deque()
_login_lock = Lock()


def limit_login(request: Request):
    now = monotonic()
    client = request.client.host if request.client else "unknown"
    with _login_lock:
        while _login_attempts and _login_attempts[0][0] <= now - 60:
            _login_attempts.popleft()
        if len(_login_attempts) >= 100 or sum(item[1] == client for item in _login_attempts) >= 10:
            raise HTTPException(status_code=429, detail="Too many login attempts; try again later", headers={"Retry-After": "60"})
        _login_attempts.append((now, client))


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

    # Block unverified enrolled accounts, including existing JWTs.
    # Preserve legacy accounts and demo compatibility.
    if not IS_DEMO:
        from app.models import UserEmailVerification

        verification_state = db.get(
            UserEmailVerification,
            user.user_id,
        )

        if (
            verification_state is not None
            and not is_email_verified(db, user)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please verify your email address before continuing.",
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
    if IS_DEMO:
        raise HTTPException(
            status_code=403,
            detail="Registration is disabled in demo mode",
        )

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
        db.flush()

        verification_token = prepare_registration_verification(
            db,
            new_user,
        )

        db.commit()

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    except Exception:
        db.rollback()
        raise

    db.refresh(new_user)

    # Email delivery happens only after the database commit.
    # Failure does not remove the successfully created account.
    deliver_registration_verification(
        recipient=new_user.email,
        token=verification_token,
    )

    return new_user


# Login and generate a JWT access token
@router.post("/login")
def login_user(
    login_data: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_login(request)
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

    # Require verification for accounts enrolled in email verification.
    # Legacy accounts without a verification record remain compatible.
    if not IS_DEMO:
        from app.models import UserEmailVerification

        verification_state = db.get(
            UserEmailVerification,
            user.user_id,
        )

        if (
            verification_state is not None
            and not is_email_verified(db, user)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please verify your email address before signing in.",
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
