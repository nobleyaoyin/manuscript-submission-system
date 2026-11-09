# Manuscript Submission System

A web-based manuscript submission and peer-review management system built with **FastAPI** and **React**.

The system supports the manuscript submission and review process through role-based access control, allowing authors, editors, and reviewers to manage their respective tasks.

## Features

### Author
- Register and log in to the system.
- Create and manage manuscript submissions.
- Upload manuscript PDF files.
- View manuscript status and editorial decisions.
- Submit revised manuscripts.
- Access manuscript version history.
- Receive notifications about manuscript updates.

### Editor
- View and manage submitted manuscripts.
- Assign reviewers to manuscripts.
- Track reviewer assignments and review progress.
- Review submitted feedback.
- Make editorial decisions: **Accept, Reject, or Request Revision**.
- Manage multiple rounds of manuscript review.
- Receive notifications about review activity.

### Reviewer
- View assigned manuscripts.
- Access manuscript files for review.
- Submit review feedback and recommendations.
- Track completed and pending assignments.
- Receive notifications about new assignments.

### Additional Features
- JWT-based authentication.
- Role-based authorization for authors, editors, and reviewers.
- PDF file upload and download.
- Manuscript revision and version tracking.
- Multiple review rounds.
- In-app notification system.
- RESTful API with interactive Swagger documentation.

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI |
| Frontend | React, JavaScript, Vite |
| Database | SQLite |
| ORM | SQLAlchemy |
| Authentication | JWT |
| API Documentation | Swagger UI |
| Version Control | Git, GitHub |

## Project Structure

```text
manuscript-submission-system/
├── app/
│   ├── main.py
│   ├── models.py
│   └── routers/
│       ├── users.py
│       ├── manuscripts.py
│       ├── assignments.py
│       ├── reviews.py
│       └── notifications.py
├── frontend/
│   ├── src/
│   │   ├── AuthorDashboard.jsx
│   │   ├── EditorDashboard.jsx
│   │   ├── ReviewerDashboard.jsx
│   │   ├── NotificationBell.jsx
│   │   └── NotificationBell.css
│   └── package.json
├── requirements.txt
├── .gitignore
└── README.md
```

*The structure above highlights the main components rather than every file in the repository.*

## Installation

### Prerequisites

Make sure the following are installed:

- Python 3.10 or later
- Node.js and npm
- Git

### 1. Clone the Repository

```bash
git clone https://github.com/nobleyaoyin/manuscript-submission-system.git
cd manuscript-submission-system
```

**Note:** This is a private repository. GitHub access is required.

### 2. Backend Setup

Create a Python virtual environment:

```bash
python3 -m venv .venv
```

Activate the environment:

**macOS / Linux**

```bash
source .venv/bin/activate
```

**Windows**

```bash
.venv\Scripts\activate
```

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

Start the FastAPI server:

```bash
uvicorn app.main:app --reload
```

The backend will be available at:

http://127.0.0.1:8000

Interactive API documentation:

http://127.0.0.1:8000/docs

### 3. Frontend Setup

Open another terminal and navigate to the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm install
```

Start the development server:

```bash
npm run dev
```

Open the local URL displayed in the terminal, typically:

http://localhost:5173

**Note:** Both the backend and frontend servers must be running for the application to function properly. Any required environment configuration must also be set up locally.

## User Roles and Workflow

The system supports three primary user roles.

**1. Author**

The author creates a manuscript submission and uploads a PDF file.

**2. Editor**

The editor reviews the submission and assigns one or more reviewers.

**3. Reviewer**

Assigned reviewers evaluate the manuscript and submit their feedback.

**4. Editorial Decision**

The editor reviews the feedback and makes a decision:

- **Accept:** The manuscript is accepted.
- **Reject:** The manuscript is rejected.
- **Request Revision:** The author is asked to revise the manuscript.

**5. Revision and Additional Review**

When revisions are requested, the author can submit an updated manuscript. The system supports additional review rounds and maintains manuscript version history.

## API Documentation

FastAPI automatically generates interactive API documentation using Swagger UI.

After starting the backend, visit:

http://127.0.0.1:8000/docs

The API includes functionality for:

- User registration and authentication
- Manuscript submission and management
- Reviewer assignments
- Review submission
- Editorial decisions
- Manuscript revisions
- Notifications

Protected endpoints require appropriate authentication and role permissions.

## Planned Publication Workflow

The system is being developed toward a complete manuscript publication workflow, from initial submission to final publication.

The planned workflow consists of three major phases and seven stages.

### Phase 1: Initial Scan and Editing

**1. Editor-in-Chief (EIC) Initial Scan**
- The EIC reviews newly submitted manuscripts.
- Authors may be asked to revise their manuscripts.
- The EIC rescans revised submissions.
- The EIC selects an initial editor and obtains the author's agreement.

**2. Initial Editing**
- The EIC invites an initial editor.
- If the invitation is declined, another editor is selected.
- The initial editor edits the manuscript.
- The author reviews the edits.
- The editor and EIC approve the edited manuscript.
- The author confirms the pre-review version.

### Phase 2: Peer Review and Decision

**3. Reviewer Assignment and Peer Review**
- The EIC invites two reviewers.
- Reviewers who decline invitations are replaced.
- Accepted reviewers submit their review reports.
- Authors revise their manuscripts based on feedback.
- The EIC checks revisions.
- Reviewers recheck revised manuscripts when required.

**4. Post-Review Decision**
- The EIC determines whether further revisions are needed.
- The review and revision cycle continues when necessary.
- Manuscripts that complete this phase proceed to final editing.

### Phase 3: Final Editing and Publication

**5. Final Editing**
- The EIC selects and invites a final editor.
- An unavailable editor may be replaced.
- The final editor prepares the manuscript.
- The author reviews and approves the final edits.

**6. Formatting**
- The EIC assigns a formatter.
- The formatter prepares the manuscript layout.
- An unavailable formatter may be replaced.
- The author reviews the formatting.
- The EIC checks the author's formatting review.

**7. DOI Assignment and Publication**
- A DOI is assigned to the manuscript.
- The publication process is completed.

### Implementation Status

The current system provides foundational manuscript submission, reviewer assignment, review, revision, editorial decision, and notification functionality.

The complete EIC-led editorial workflow, invitation acceptance process, initial and final editing stages, formatting workflow, and DOI assignment are planned enhancements.

These features will be implemented incrementally as development progresses.

## Development

This project is under active development.

Current work focuses on improving the manuscript submission workflow, reviewer assignment management, editorial decision handling, and user notifications.

Future improvements may include additional editorial workflow features, enhanced user interfaces, and further testing.

## Security and Local Data

Local environment files, databases, uploaded manuscripts, and dependency directories are excluded from version control through `.gitignore`.

Developers should use their own local configuration and test data. Sensitive credentials and private manuscript files should not be committed to the repository.

## Project Information

**Project:** Manuscript Submission System

**Backend:** FastAPI

**Frontend:** React

**Database:** SQLite

**Repository:** https://github.com/nobleyaoyin/manuscript-submission-system