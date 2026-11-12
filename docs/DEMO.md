# Isolated local Demo

The Demo uses a new synthetic SQLite database and upload directory, with separate generated accounts and signing key. It does not need the Development database or uploads. The gateway listens on localhost only; registration, API documentation and real email delivery are disabled in Demo mode. Do not start a public tunnel or expose the application without separate explicit approval.

Run these commands from the repository root after installing the dependencies in [README](../README.md). Build the React frontend first:

```bash
cd frontend
npm ci
npm run build
cd ..
```

## Initialize once

Choose a **new** absolute canonical path outside existing data. For example, in a new checkout on macOS or Linux:

```bash
.venv/bin/python scripts/init_demo.py --data-root "$PWD/.demo_data"
```

This is for a new Demo only. The initializer refuses an existing directory; it does not restore or reset one. It creates five synthetic roles (Author, Editor, Reviewer, EIC and Formatter), a synthetic draft/PDF, a private accounts file and a separate signing key. It creates the base schema and applies migrations 001–006 to this new database only. It does not apply 007–009. The generated account credentials are stored locally in `.demo_data/accounts.json`; keep that file and `.demo_data/jwt.key` private and never commit or post their contents. Never copy Development data into the Demo.

## Start, restart and stop

```bash
.venv/bin/python scripts/run_demo.py --data-root "$PWD/.demo_data" --port 8765
```

Check the printed database and upload paths, then visit `http://127.0.0.1:8765`. The gateway serves the built frontend and `/api` on the same origin. The EIC dashboard and Formatter dashboard are present; Formatter currently uses a separate sign-in link on the role-selection page. Log in using the locally generated Demo account for the desired role.

Stop the gateway with Ctrl-C. Restart it with the **same** data-root path to retain committed Demo records. Do not rerun initialization, delete the directory, regenerate keys or migrate it without specific authorization. If records appear missing, first verify the selected root, API origin and signed-in account; do not reset data. Login is rate-limited in memory per process and connecting address. This local limiter is not a public access gate.

If a public meeting setup is requested later, obtain separate approval and complete a security review before any tunnel or external exposure. This guide does not authorize or start one.
