# Step 128.27.23 — Synthetic Demo Persistence

## Result

**Passed for a newly initialized synthetic Demo root:** a normally stopped and restarted local FastAPI Demo gateway, using the exact same temporary data root, retained the tested committed records and synthetic upload bytes. This does not establish the cause of any missing records in an existing Demo, which was not accessed.

## Method

`/private/tmp/gsub-step128-27-23-tools/baseline.py` copied application source, migrations, scripts and a pre-existing frontend build into a unique temporary project directory; it did not copy an existing database, uploads or credential files. It initialized a separate temporary Demo root with migrations 001–006, seeded only additional synthetic reviewer user accounts, started the actual gateway on an ephemeral localhost port, and used real authenticated API actions to create two manuscripts and progress the workflow. The test did not initialize, inspect, restart or migrate any existing `.demo_data` or other environment. A headless browser verified the Author role selection/login and saw the new synthetic manuscript before restart.

Before restart, read-only queries against the **temporary** SQLite database returned: users 7; manuscripts 3 (one initializer draft plus two API-created); review assignments 6; reviews 6; review round files 3; review assignment file links 6; author agreements 2; local prototype publications 1. Roles were author 1, editor 1, EIC 1, formatter 1, reviewer 3. Eight synthetic upload files were enumerated, with relative paths and SHA-256 fingerprints kept within the test process. No names, passwords, JWTs or private contents were printed.

The gateway was stopped via normal termination and restarted with the same `--data-root`; Author and Reviewer logged in again. API reads verified manuscript #2 remained published, the synthetic local proof was downloadable, and manuscript #3's first-round PDF and second-round DOCX still matched the originally bound bytes. The same temporary SQLite table counts, upload path set and all eight SHA-256 fingerprints matched before/after restart. One representative proof SHA-256 was `58250304552c62d59e46b1cdae28edafaea589bbbc5a1d4f552da1c4ce965e34`; round-one and round-two hashes are in the workflow report. The harness terminated the gateway and removed the temporary data root. It did not re-open the browser after restart; browser persistence, every role's post-restart views, every table's full contents and every possible history endpoint were not independently checked.

## Commands and limitations

The first invocation of `.venv/bin/python -B /private/tmp/gsub-step128-27-23-tools/baseline.py "$PWD"` had a duplicate synthetic account fixture; the second was stopped by sandbox localhost socket denial. After using the available permission mechanism, a run reached gateway startup and exposed a 422 for a reserved-domain synthetic email fixture. Both fixtures were corrected only in the temporary harness. The final approved invocation passed the workflow and restart checks described above. No application source changes were made. The test used the existing built frontend; source/build freshness was not established. The synthetic Demo gateway disabled email; outbound traffic was not separately instrumented.

## Interpretation and follow-up

The observed synthetic restart does not explain the user's prior missing-record report. Different configured root/mode, backend/API origin or process, account ownership filters, stale UI state, or failed writes remain hypotheses, not findings. If a future synthetic reproduction is insufficient, obtain explicit separate approval before examining an existing environment's identity, process, configuration and aggregate read-only data. Never reset, reinitialize, migrate, copy or restore an existing root as part of this task. Any backup or recovery also requires explicit authorization. See `reports/step_128_27_23_workflow_baseline.md` for stage results and `reports/step_128_27_23_blocker_list.md` for prioritized gaps.
