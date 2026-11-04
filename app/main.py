
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import users, manuscripts, assignments, reviews


# Create FastAPI application
app = FastAPI(
    title="Manuscript Submission System",
    description="CSE 472 Project II",
    version="0.1.0",
    swagger_ui_parameters={
        "persistAuthorization": True
    }
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
    allow_headers=["*"]
)


# Register routers
app.include_router(users.router)
app.include_router(manuscripts.router)
app.include_router(assignments.router)
app.include_router(reviews.router)


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
