from datetime import datetime
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# Data received when a user registers
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


# Data returned to the client
class UserResponse(BaseModel):
    user_id: int
    name: str
    email: EmailStr
    role: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Data received when a user logs in
class UserLogin(BaseModel):
    email: EmailStr
    password: str


# Schema for creating a manuscript
class ManuscriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    abstract: str = Field(min_length=1)


# Schema for returning manuscript information
class ManuscriptResponse(BaseModel):
    manuscript_id: int
    title: str
    abstract: str
    author_id: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ManuscriptUpdate(BaseModel):
    title: str | None = None
    abstract: str | None = None

class ReviewAssignmentCreate(BaseModel):
    manuscript_id: int
    reviewer_id: int
    review_round: int = Field(default=1, ge=1)

class ReviewAssignmentResponse(BaseModel):
    assignment_id: int
    manuscript_id: int
    reviewer_id: int
    assigned_by: int
    review_round: int
    status: str
    assigned_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ReviewCreate(BaseModel):
    assignment_id: int
    comments: str
    recommendation: Literal["accept", "revision", "reject"]


class ReviewResponse(BaseModel):
    review_id: int
    assignment_id: int
    comments: str
    recommendation: str
    submitted_at: datetime

    model_config = ConfigDict(from_attributes=True)

class EditorDecision(BaseModel):
    decision: Literal[
        "accepted",
        "rejected",
        "revision_required"
    ]

class ReviewHistoryResponse(ReviewResponse):
    review_round: int
    reviewer_id: int