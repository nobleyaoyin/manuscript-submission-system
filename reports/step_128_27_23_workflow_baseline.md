# Step 128.27.23 — Full Workflow Baseline

## Outcome and scope

A fresh synthetic Demo environment completed a **real HTTP API** manuscript path from author-created draft through local prototype publication, and a second newly created manuscript completed revision, a second review round and EIC acceptance. Same-root process restart preserved the tested records, round-specific downloads and all temporary upload hashes. This is **not a full browser end-to-end pass**: the actual built React UI was checked only for role selection and Author login/manuscript visibility. There was no current browser verification of the other dashboard actions, readability, mobile views or complete workflow.

No existing Development, Demo, Sandbox, Stable-Demo or production database, upload, credential or `.demo_data` was accessed or changed. No workflow record was directly inserted to bypass a stage; fixture insertion was limited to two additional synthetic reviewer users. No real emails, AI providers, OAuth, CAPTCHA, DOI registration, commit, push or deployment occurred.

## Isolation and execution method

The temporary harness `/private/tmp/gsub-step128-27-23-tools/baseline.py` copied only active `app/`, `migrations/`, `scripts/` and the pre-existing `frontend/dist/` into a newly generated `/private/tmp/gsub-baseline-*` project tree; it did **not** copy any database, uploads, dotenv file or credentials. The isolated `scripts/init_demo.py` created a new canonical, private root, synthetic PDF and five synthetic role accounts, and applied migrations 001–006 only to the new temp SQLite database. Two additional synthetic reviewer accounts were inserted into that temporary database as account fixtures. The actual FastAPI Demo gateway was started on a random localhost port; real API calls used authenticated synthetic accounts. The pre-existing built frontend bundle was served, but its freshness relative to current frontend source was not independently established. The gateway was stopped and restarted normally against the same root. The harness stopped its process and removed its temporary data tree in `finally`.

The test used Demo mode, which disables registration and email delivery; it did not make actual outbound provider calls. It used a controlled environment with synthetic keys and disabled dotenv loading, but did **not** instrument or prove that all external networking was blocked. Browser HTTP and gateway traffic were localhost only. The synthetic PDFs were signature-valid test bytes, not rendering-quality manuscript files; DOCX was a minimal ZIP containing required structure. Credentials and JWTs were kept within the temporary test process and are not reproduced here.

## Stage-by-stage results

| Stage | Current result and evidence | Mode / limitation |
|---|---|---|
| Synthetic users / login | Seven synthetic accounts: author, editor, EIC, formatter and three distinct reviewers; all logged in through `/users/login`. | API; staff/reviewer fixture setup uses temporary SQLite, not account-management UI. Registration not tested in Demo (disabled). |
| Author draft, initial PDF, submit | Created new manuscript #2 with `/manuscripts/`, verified draft, uploaded PDF, formally submitted and verified submitted state. | API; no seeded manuscript workflow records. |
| EIC initial screening | EIC recorded `pass` using `/eic/manuscripts/{id}/initial-scan`. | API. |
| Initial editor invitation and acceptance | EIC invited the synthetic editor, editor accepted. | API. Preliminary author consent to editor is not implemented and was not invented. |
| Initial edit and approvals | Editor uploaded distinct PDF; author review, editor approval, EIC approval and author pre-review confirmation succeeded in required order. | API. |
| Two reviewer invitations and downloads | Assigned two distinct reviewers; pending reviewer download rejected (403/409), each accepted and downloaded exact confirmed initial PDF bytes and a `Content-Disposition` filename. Third unassigned reviewer denied (403/404). | API; no browser reviewer download in this run. |
| Reviews / EIC decision | Both reviewers submitted reviews; duplicate review returned 409; EIC accepted. | API. Two-reviewer and invitation behavior also covered by isolated regressions. |
| Author agreement | Exactly retrieved generated agreement; author accepted matching version and repeat response returned 409. | API; agreement remains a prototype placeholder. |
| Final editor | EIC invited, editor accepted and uploaded PDF; EIC and author approved. | API. Correction/decline loops covered by isolated fixture regressions, not this continuous run. |
| Formatting and local publication | EIC invited formatter, formatter accepted and downloaded exact final PDF; uploaded proof; author and EIC approved; EIC recorded publication with `doi_status=pending_external`; duplicate publish rejected and author downloaded exact proof. | API; no real DOI or external publication. |
| Separate revision and subsequent review round | Manuscript #3 repeated initial stages and two reviewer reviews; EIC requested revision, author uploaded DOCX and formally resubmitted, two distinct reviewers accepted/finished round two, EIC accepted. Round-one download still matched its original PDF after round two. | API. Does not demonstrate an independent explicit recheck policy or browser revision workflow. |
| Browser / UI | Headless Chromium loaded the actual served bundle, found the Author card and separate Formatter sign-in link, selected Author, signed in, and saw the synthetic published manuscript. | Browser only for these observations. No full dashboard, contrast or mobile audit. Four primary cards and secondary Formatter link remain the known Priority A gap. |
| Restart | Gateway stopped and restarted with same temporary root; author and reviewer logged in again; published status, round-one PDF, round-two DOCX, proof bytes, aggregate table counts and all 8 upload path/hash pairs matched. | API and read-only synthetic SQLite/filesystem comparisons; see persistence report. |

## File integrity and permissions

- Confirmed round-one initial-edit PDF SHA-256: `ccdfa2aeb94d1939d666111f65cbfeeedda7facca8a7ca2175afdcf468802a5d`.
- Round-two synthetic revised DOCX SHA-256: `6668f54c0dfe9d80fb08c56dffd89c094e661b5db4f909ebceb999a1689f2eb4`.
- Approved published synthetic proof SHA-256: `58250304552c62d59e46b1cdae28edafaea589bbbc5a1d4f552da1c4ce965e34`.
- The test compared actual response bytes to the synthetic source bytes and checked an attachment filename existed, but did not assert the exact filename string or exact extension in this continuous run. Assignment-to-round correctness was exercised via different response bytes, not a separate SQL join assertion. All 8 synthetic upload paths and hashes matched across restart.
- Unauthorized reviewer download and pending invitation download were denied; duplicate review, agreement response and publication were rejected. This does not constitute exhaustive role, history, general-download or version endpoint security coverage.
- Current source permits author PDF/DOCX/DOC, but not TXT; Initial Editing, Final Editing and Formatter upload/confirmation paths remain PDF-only. DOCX was exercised in round two here; DOC and TXT were not newly tested in the continuous scenario. The existing isolated reviewer-download suite covers additional synthetic PDF/DOCX/DOC cases; it is not a full UI test of every upload stage.

## Commands and actual results

1. `.venv/bin/python -B /private/tmp/gsub-step128-27-23-tools/baseline.py "$PWD"` initially failed during synthetic fixture setup with `UNIQUE constraint failed: users.email` because the test helper duplicated rows; no application workflow was reached. Corrected the **temporary harness only**.
2. The same command then initialized the temporary Demo but the sandbox denied binding a localhost socket (`PermissionError: [Errno 1] Operation not permitted`). A permission request was made and the isolated command rerun with approval. Its first approved execution reached gateway startup but stopped at HTTP 422 for a synthetic reserved-domain reviewer address. Corrected the temporary fixture address only.
3. Approved rerun of `.venv/bin/python -B /private/tmp/gsub-step128-27-23-tools/baseline.py "$PWD"` printed PASS for synthetic initialization, gateway, all synthetic logins, initial workflow, review authorization/bytes, local publication, separate revision/round two, browser role/login, normal restart and persistence; 0 workflow blockers in that run. Harness process completed and removed its temporary data root.
4. `PYTHON_DOTENV_DISABLED=1 SECRET_KEY=synthetic-test-only PYTHONPATH=tests .venv/bin/python -B -m unittest tests.test_initial_editing tests.test_reviewer_invitations tests.test_two_reviewer_policy -q` — **50 passed**, 0 failed, 0 skipped.
5. `PYTHON_DOTENV_DISABLED=1 SECRET_KEY=synthetic-test-only PYTHONPATH=tests .venv/bin/python -B -m unittest tests.test_author_agreement tests.test_final_workflow tests.test_formatting -q` — **55 passed**, 0 failed, 0 skipped.
6. `PYTHON_DOTENV_DISABLED=1 SECRET_KEY=synthetic-test-only PYTHONPATH=tests .venv/bin/python -B -m unittest tests.test_reviewer_download -q` — **6 passed**, 0 failed, 0 skipped.

Existing regression fixtures were inspected: temporary directories/SQLite, dependency overrides and synthetic files. Counts include inherited/repeated cases; some stage suites seed workflow fixtures, unlike the continuous gateway scenario. SQLAlchemy emitted an existing `datetime.utcnow()` deprecation warning. No full frontend test suite or new application build was run; no tests were skipped within these three commands.

## Files and next steps

Created three reports: `reports/step_128_27_23_workflow_baseline.md`, `reports/step_128_27_23_demo_persistence.md`, `reports/step_128_27_23_blocker_list.md`. Created the standalone temporary harness at `/private/tmp/gsub-step128-27-23-tools/baseline.py`; no application, frontend, migration or configuration file was modified. Continue only after owner review. Priority A readability, fifth Formatter card, four-format uploads, sidebar/progress, reviewer directory, tie handling and anonymity, as well as AI and Priority B/C items, remain on the approved roadmap.
