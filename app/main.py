from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, BASE_DIR
from sqlalchemy import inspect, text
from app import models
from app.config import IS_DEMO
from app.routers import users, manuscripts, assignments, reviews, notifications, eic, agreements, final_workflow, formatting, email_verification


# Initialize database tables when the application starts
@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine.url.database != ":memory:" and not Path(engine.url.database).is_file():
        raise RuntimeError("Database does not exist; initialize it and apply migrations before startup")
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        required = {"schema_migrations", "review_round_files", "review_assignment_files"}
        if not required.issubset(tables) or not connection.execute(
            text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"),
            {"id": "001_reviewer_file_bindings"},
        ).first():
            raise RuntimeError("Database migration 001_reviewer_file_bindings is required before startup")
        if "reviewer_invitations" not in tables or not connection.execute(text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"), {"id": "002_reviewer_invitations"}).first():
            raise RuntimeError("Database migration 002_reviewer_invitations is required before startup")
        if "manuscript_review_policies" not in tables or not connection.execute(text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"), {"id": "003_two_reviewer_policy"}).first():
            raise RuntimeError("Database migration 003_two_reviewer_policy is required before startup")
        if "author_agreements" not in tables or not connection.execute(text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"), {"id": "004_author_agreements"}).first():
            raise RuntimeError("Database migration 004_author_agreements is required before startup")
        if not {'final_editor_invitations', 'final_editing_submission_invitations'}.issubset(tables) or not connection.execute(text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"), {"id": "005_final_editor_workflow"}).first():
            raise RuntimeError("Database migration 005_final_editor_workflow is required before startup")
        if not {"formatter_invitations", "formatting_submissions", "prototype_publications"}.issubset(tables) or not connection.execute(text("SELECT 1 FROM schema_migrations WHERE migration_id = :id"), {"id": "006_formatter_publication"}).first():
            raise RuntimeError("Database migration 006_formatter_publication is required before startup")
    yield


# Create FastAPI application
app = FastAPI(
    title="Manuscript Submission System",
    description="CSE 472 Project II",
    version="0.1.0",
    swagger_ui_parameters={
        "persistAuthorization": True
    },
    lifespan=lifespan,
    docs_url=None if IS_DEMO else "/docs",
    redoc_url=None if IS_DEMO else "/redoc",
    openapi_url=None if IS_DEMO else "/openapi.json",
)


# Allow React frontend to communicate with FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# Register routers
app.include_router(users.router)
app.include_router(email_verification.router)
app.include_router(manuscripts.router)
app.include_router(assignments.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(eic.router)
app.include_router(agreements.router)
app.include_router(final_workflow.router)
app.include_router(formatting.router)


# Home endpoint
@app.get("/")
def home():
    return {
        "message": "Welcome to the Manuscript Submission System"
    }


# Health check endpoint
@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }
