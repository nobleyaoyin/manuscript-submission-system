# G-Sub Manuscript Submission System

G-Sub is a working name for a classroom manuscript submission and journal workflow project. The backend uses **FastAPI, SQLAlchemy, and SQLite**; the frontend uses **React and Vite**. Authentication uses JWTs and server-side role checks.

## Current functionality

The application has Author, Editor, Editor-in-Chief (EIC), Reviewer, and Formatter backend roles and dashboards. Authors can create drafts, upload, submit, revise, and track manuscripts. The workflow includes EIC screening, initial editing, reviewer invitations and round-specific files, review decisions, a post-acceptance author agreement, final editing, formatting reviews, and a **local publication prototype**. In-app notifications are available.

Author manuscript uploads currently accept PDF, DOCX, and DOC up to 10 MiB. TXT is not yet supported. Initial-editing, final-editing and formatting uploads currently require PDF. A reviewer receives the file bound to their assignment's review round. Existing historical assignments without reliable bindings can show “File Unavailable”.

The agreement text is a placeholder; local publication does not publish externally and the DOI status is pending external registration. AI screening, configurable reviewer anonymity, universal four-format uploads, real DOI registration and some workflow improvements remain planned. Email verification has a restricted development integration; workflow email enqueueing is disabled by default. Do not assume a feature is deployment-ready merely because its code exists. See the [remaining-work audit](reports/step_128_27_22_remaining_work_audit.md) and [demo roadmap](reports/step_128_27_22_demo_roadmap.md).

## Prerequisites and dependencies

Use Python 3.12 and a Node.js version supported by the current Vite dependencies (Node 20.19+, 22.12+, or 24+). Git and npm are required. From a new checkout:

```bash
git clone https://github.com/nobleyaoyin/manuscript-submission-system.git
cd manuscript-submission-system
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The pinned requirements, including the startup import `resend`, were installed and checked in a fresh Python 3.12 environment on macOS using PyPI. Python 3.9 was unable to resolve the `annotated-types==0.8.0` pin; ensure that `python3.12` really invokes Python 3.12. Other platforms and package indexes have not been verified, and the pinned `uvloop` dependency may limit Windows installation. No provider credentials are needed for the isolated Demo.

```bash
cd frontend
npm ci
npm run build
cd ..
```

## First-time local data setup

**Start with the fresh isolated Demo** described in [docs/DEMO.md](docs/DEMO.md). It creates a new synthetic SQLite database, uploads and five demo-only accounts in a separately selected new directory; the initializer refuses an existing directory. The initializer applies migrations 001–006 to its new database. Do not rerun it to restore a previous demo.

There is currently **no documented, verified command to initialize an empty ordinary Development database**. Development mode expects a pre-existing `manuscript.db` at the project root with the base schema and migrations 001–006; the email-verification features have additional schema requirements. Application startup checks migrations and does not initialize or migrate an existing database. Do not copy a teammate's private database or use `create_all()` to repair one. Any initialization or migration procedure for an existing database requires an independently verified backup, isolated rehearsal, and explicit owner approval. Until a supported fresh Development initializer is documented, use the isolated Demo for first-time exploration.

## Start the application

For the isolated Demo, follow [docs/DEMO.md](docs/DEMO.md). Its gateway serves the built frontend and FastAPI under one localhost origin.

For ordinary Development **only after a compatible local database has been safely prepared**, set a private signing key in your shell and start the backend:

```bash
export SECRET_KEY='<your-own-private-random-signing-key>'
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Replace the placeholder with a strong randomly generated secret; never commit or share it. An optional private `.env` may be used locally. Never use the Demo key for Development. On Windows, use the equivalent virtual-environment executable and environment-variable command.

In a second terminal, start Vite from the repository root:

```bash
cd frontend
npm run dev
```

Vite normally serves `http://localhost:5173` and proxies `/api` to the backend at `127.0.0.1:8000`. Development API documentation is at `http://127.0.0.1:8000/docs`; Demo mode disables public registration and API documentation. Do not start a public tunnel or deploy without explicit approval.

## Isolated tests

The following backend suites use temporary SQLite databases and temporary upload directories. Run from the project root with a synthetic signing key; they do not require provider access:

```bash
PYTHON_DOTENV_DISABLED=1 SECRET_KEY=synthetic-test-only PYTHONPATH=tests \
  .venv/bin/python -B -m unittest tests.test_initial_editing tests.test_reviewer_invitations tests.test_two_reviewer_policy -q
PYTHON_DOTENV_DISABLED=1 SECRET_KEY=synthetic-test-only PYTHONPATH=tests \
  .venv/bin/python -B -m unittest tests.test_author_agreement tests.test_final_workflow tests.test_formatting tests.test_reviewer_download -q
```

These commands use a synthetic test-only value, not an application credential. Browser tests additionally require Playwright and a compatible browser; do not point them at existing environments. Do not run the root `test_manuscript.py` against valuable data: it can write to the configured database. Inspect unfamiliar test fixtures before running broad test discovery.

## Data and repository safety

Keep environment files, signing keys, database files and sidecars, uploads, generated Demo credentials, backups, virtual environments and `node_modules` out of Git. Never reset, overwrite, migrate or restore another person's database or uploads without explicit permission. Do not send real email or connect to external services merely to run the local Demo. The planned work is tracked in the [demo roadmap](reports/step_128_27_22_demo_roadmap.md); the [baseline report](reports/step_128_27_23_workflow_baseline.md) distinguishes API coverage from browser coverage.
