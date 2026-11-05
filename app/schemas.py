
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    ConfigDict,
    field_validator,
)


# Data received when a user registers
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Name cannot be empty.")

        return value


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

    @field_validator("title", "abstract")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("This field cannot be empty.")

        return value


# Schema for returning manuscript information
class ManuscriptResponse(BaseModel):
    manuscript_id: int
    title: str
    abstract: str
    author_id: int
    status: str
    created_at: datetime

    # Whether the manuscript has an available PDF
    has_pdf: bool = False

    model_config = ConfigDict(from_attributes=True)


# Schema for updating a manuscript
class ManuscriptUpdate(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    abstract: str | None = Field(
        default=None,
        min_length=1,
    )

    @field_validator("title", "abstract")
    @classmethod
    def validate_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        if not value:
            raise ValueError("This field cannot be empty.")

        return value


# Schema for assigning a reviewer
class ReviewAssignmentCreate(BaseModel):
    manuscript_id: int = Field(gt=0)
    reviewer_id: int = Field(gt=0)
    review_round: int = Field(default=1, ge=1)


# Schema for returning reviewer assignment information
class ReviewAssignmentResponse(BaseModel):
    assignment_id: int
    manuscript_id: int
    reviewer_id: int
    assigned_by: int
    review_round: int
    status: str
    assigned_at: datetime

    # Additional manuscript information for the reviewer dashboard
    manuscript_title: str | None = None
    manuscript_status: str | None = None
    has_pdf: bool = False

    model_config = ConfigDict(from_attributes=True)


# Schema for submitting a review
class ReviewCreate(BaseModel):
    assignment_id: int = Field(gt=0)
    comments: str = Field(min_length=1)
    recommendation: Literal[
        "accept",
        "revision",
        "reject",
    ]

    @field_validator("comments")
    @classmethod
    def validate_comments(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Review comments cannot be empty.")

        return value


# Schema for returning review information
class ReviewResponse(BaseModel):
    review_id: int
    assignment_id: int
    comments: str
    recommendation: str
    submitted_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Schema for editor decisions
class EditorDecision(BaseModel):
    decision: Literal[
        "accepted",
        "rejected",
        "revision_required",
    ]


# Schema for review history
class ReviewHistoryResponse(ReviewResponse):
    review_round: int
    reviewer_id: int
