# Step 128.27.22 — G-SUB Demo-First Roadmap

## Executive summary

Deliver a stable, understandable classroom demonstration by validating and connecting the existing workflow, improving the shared visual experience, and addressing reviewer selection, anonymity and tie handling. The owner additionally requires Priority A global readability, five primary login-role cards including Formatter, and PDF/DOCX/DOC/TXT uploads at all applicable existing document-upload stages. Defer advanced lifecycle administration and real email delivery unless a confirmed demo blocker makes a narrowly scoped fix necessary. This is a proposed finite sequence, **not authorization to implement, migrate, expose or deploy**. Current-code evidence and the full feature/status/impact matrix are in [the companion audit](step_128_27_22_remaining_work_audit.md). Code existence does not certify a current end-to-end pass.

Priority A targets the requested course demonstration; B preserves prior core requirements; C records advanced or external integrations for future approval. Steps can be reordered based on the first synthetic test findings. Estimates are relative: S small, M medium, L large. Every phase uses temporary SQLite and uploads, synthetic users and mocked external services; no existing database, uploads or credentials are touched without separate explicit authorization.

## Priority A — Required for the course demonstration

### A1. Establish a synthetic full-workflow and restart baseline — M–L; recommended next

- **Objective:** Verify the actual FastAPI and React workflow from a new author draft/submission through initial scan/editing, two accepted reviews, EIC decision, agreement, final editing, formatting and local prototype publication. Include one revision round and correct round-bound file bytes; do not repeat a test against a real environment.
- **Deliverables:** Isolated browser/API scenario, test report mapping browser versus API versus fixture setup, and synthetic same-root restart/persistence check. Provision synthetic accounts and documents only; no direct seeding of intermediate workflow records to conceal missing stages. If a step is blocked, record the exact failure and continue independent checks rather than inventing transitions.
- **Dependencies / decisions:** Test fixture must support two distinct reviewers, editor/EIC/formatter and a separate temp upload root; actual browser availability. Confirm the local publication prototype is acceptable for the course demonstration.
- **Acceptance:** Earlier and later review rounds download the correct bytes and filenames; permissions hold; agreement/final/proof handoffs and local publication work or the blocker is captured; after restart, committed synthetic records/files are visible to the correct accounts with hashes unchanged. Explicitly report untested branches.
- **Tests:** Headless browser where safe; API regressions, unauthorized reads, duplicate actions, restart and history; no real provider or email. **Schema:** use existing migrations only on synthetic database. **Approval:** next task approval; separately request approval for any actual-environment diagnostic or migration. Do not combine unrelated fixes.

### A2. Fix only confirmed demonstration blockers — size driven by A1

- **Objective / deliverables:** Triage each reproducible A1 failure into a separate small code change and report; preserve existing workflows and local changes.
- **Dependencies:** A1 reproduction and owner approval for significant workflow/schema decisions. No artificial fixture bypasses.
- **Acceptance / tests:** Original failing case passes in isolation, relevant surrounding stage/security regressions pass, no unintended historical or file changes. **Schema:** only if proven necessary and separately approved; migration tested on disposable databases. **Approval:** per scoped task and any migration/architectural change.

### A2a. Global text readability and contrast — M

- **Objective / deliverables:** Audit and fix computed text/foreground contrast across login and role selection, all five dashboards, sidebar/navigation, buttons/forms/inputs/placeholders, tables/cards/modals/badges, notifications/progress/errors, and disabled/selected/hover/focus states on light and dark surfaces. Examine `index.css`, `App.css`, `RoleSelection.css`, dashboard and panel CSS, `NotificationBell.css` and inherited/native control colors; see the detailed companion audit. A palette change alone does not meet this requirement.
- **Dependencies:** A1 screenshots and A3 shared-shell design; coordinate tokens and test both before and after shell changes. No backend changes expected.
- **Acceptance / tests:** Measure real foreground/background pairs; target WCAG AA 4.5:1 ordinary text and 3:1 qualifying large text and applicable control/focus indicators, with disabled state distinguishability where practical. Test desktop/mobile, browser light/dark preferences, zoom, accessible labels, keyboard/focus and visual/screenshot regression for each role, alert and interactive state; verify semantic state is not color-only. **Schema:** none. **Approval:** normal scoped frontend task.

### A2b. Five primary role-selection cards — S

- **Objective / deliverables:** Add Formatter as a fifth visible, primary card alongside Editor-in-Chief, Editor, Peer Reviewer and Author / Submitter in `RoleSelection.jsx`, with matching `RoleSelection.css` and existing formatter labels. The current secondary Formatter link is not sufficient.
- **Dependencies:** A2a accessible colors and focus treatment; existing `App.jsx` login and authenticated role dispatch. Five cards are an owner requirement; exact responsive arrangement is a routine design choice.
- **Acceptance / tests:** All five cards use the existing login flow; desktop/mobile layout, keyboard navigation, visible focus and Formatter login work. Wrong-role login explains the mismatch; selecting or manipulating a card never grants, creates or changes backend roles. **Schema:** none. **Approval:** normal scoped frontend task; preserve authentication.

### A2c. Universal multi-format manuscript-document uploads — L

- **Objective / deliverables:** Extend **each existing authorized** author initial/revision, assigned Initial Editor, assigned Final Editor and Formatter document/proof upload to PDF, DOCX, DOC and TXT. Existing reviewer attachment and independent EIC upload capabilities were not found; do not create new permissions. Detailed endpoint/format inventory and PDF-specific constraints are in the companion audit addendum.
- **Dependencies:** A1 baseline and a reviewed design for safe content validation, exact version/original filename metadata, first-round confirmed Initial Editing binding, formatter proof/publication rules and any PDF-only preview. Owner approval is needed for workflow/proof semantics and any additive migration. Mandatory four-format upload support is already a requirement; the open decision is how approved artifacts and any required PDF proof coexist, not whether to silently reject a required format.
- **Acceptance / tests:** Existing authorized roles can upload all four types at every applicable stage; retain the 10 MB limit unless separately approved. Validate extension and contents with bounded processing, store unique immutable artifacts, preserve old files and every reviewer binding, return correct MIME and safe original filename where recorded, and provide an authenticated download fallback when safe preview is unavailable. Existing EIC/author confirmations, SHA-256 and exact approved publication file remain enforced. No silent conversion, replacement or deletion. Test each format and stage, malformed/spoofed content, empty/oversize files, encoding, unauthorized roles, previews/downloads and filenames, first/later round bindings, approval/correction flows and historical PDF regression. **Schema:** investigate additive filename/type metadata; never rewrite old bindings. **Approval:** proof/conversion policy, any workflow/security dependency and migration.
- **Practical format handling:** PDF signatures; bounded DOCX ZIP/OOXML validation; DOC OLE structure with a vetted parser if stronger validation is required; TXT defined encoding and binary rejection. Browser DOC preview or legacy DOC parsing may need an optional sandboxed dependency; otherwise use download, not unreviewed conversion. No external document service is implied.

### A3. Shared demo shell and visual progress — M

- **Objective / deliverables:** Reuse a common responsive shell across existing dashboards with accessible sidebar, top-left approved G-SUB branding, cohesive colors, clear section boundaries, concise summary and role-authorized statistics. Reuse `ManuscriptProgress.jsx` for a vertical timeline near the selected manuscript header, with brief evidence-based next actions.
- **Dependencies:** Agree logo asset, navigation scope, statistics meaning and role-specific visibility; existing status mapping deliberately treats ambiguous history as unknown.
- **Acceptance / tests:** Desktop/mobile/keyboard navigation, visible focus, current selected manuscript and role-safe totals; unknown historical stages remain unknown; no dashboard action regresses. Browser UI/accessibility, fetch failure, switching and privacy tests. **Schema:** none expected. **Approval:** visual scope/logo and any new API or statistics semantics if needed.

### A4. Reviewer selection and additional-reviewer UX — M

- **Objective / deliverables:** A role-scoped reviewer directory/select control showing authorized reviewer name and professional title, and clear duplicate/existing-invitation status. Offer an additional reviewer through the existing assignment capability when appropriate.
- **Dependencies:** Decide title source and optionality, whether email is visible to EIC/assigned editor, staff provisioning, and the professor's 50/50 interpretation. Existing `ReviewerInvitationPanel.jsx` takes numeric IDs; existing backend allows multiple assignments but blocks decision until non-declined assignments finish.
- **Acceptance / tests:** Only authorized EIC/assigned editor obtains minimal directory data; selected identity comes from server and not UI role claims; duplicate requests fail server-side; simulate split recommendations, invitation, acceptance/decline/replacement and any approved decision rules. **Schema:** optional title storage may require additive migration; plan separately. **Approval:** selection/disclosure, split policy and schema if needed.

### A5. Configurable reviewer anonymity — L

- **Objective / deliverables:** Implement an EIC-controlled full/partial anonymity policy only once meanings are explicit; protect API payloads, dashboard views, files and history, not merely client labels.
- **Dependencies:** Owner/professor decide who sees author/reviewer identity, whether identity within uploads is in scope, when policy locks, and how history/legacy cases behave. Automatic file redaction is not assumed.
- **Acceptance / tests:** Document a role-by-resource matrix; real API negative tests cover author, reviewer, other reviewer, assigned editor and EIC for identities, reports, versions and downloads; switching modes cannot retroactively leak data. Display a clear limitation if manuscript files themselves are not redacted. **Schema:** likely additive per-manuscript/round policy. **Approval:** policy, workflow and migration separately.

### A6. File-editing interpretation, notification essentials and meeting runbook — M

- **Objective / deliverables:** With professor guidance choose mandatory revision request/upload or actual in-system editing; implement only the chosen small scope. Fill confirmed in-app submission, revision, formatting and publication notice gaps and update stale `docs/DEMO.md`.
- **Dependencies:** Define recipients, action triggers and immutable file/version behavior; avoid expanding into email transport. A1/A2 identify real missing notices and file workflow.
- **Acceptance / tests:** Required revisions are visible to the correct author, original files and round bindings remain intact, notices are transactional and role-scoped, runbook clearly describes local startup/restart and prototype publication. Test duplicate notices, read/unread, authorization, revision bytes and synthetic restart. **Schema:** none for simple notifications/revision actions unless a new state is approved; embedded editing may need much more. **Approval:** editor interpretation, any workflow/schema change.

## Priority B — Previously requested core enhancements

### B1. Initial-editor preliminary author consent — M

- **Objective / deliverables:** Implement the earlier consent step separately from the post-acceptance publication agreement, only after agreeing the exact order and decline/reselection behavior.
- **Dependencies:** A1 baseline, original workflow and owner decision; historical compatibility and notification recipients.
- **Acceptance / tests:** Only manuscript author responds; consent/refusal and editor selection are audited, duplicate/concurrent responses safe, no regression of existing Stage 3 or historical records. **Schema:** likely additive migration, explicit approval. **Approval:** transition semantics and migration.

### B2. Revision recheck and safe participant replacement — M–L

- **Objective / deliverables:** Make EIC revision recheck and reviewer reassignment explicit; define whether prior reviewers are reinvited and how unavailable accepted participants are handled.
- **Dependencies:** A4, policy for participant revocation, current round identity and historical review preservation; lifecycle prototype is not active.
- **Acceptance / tests:** Correct reviewed version each round, EIC next action and history clear, only authorized participants can access, conflicts and concurrent response/round advance do not corrupt progress. **Schema:** possible additive audit/transition migration. **Approval:** accepted-reviewer/editor/formatter removal and migration.

### B3. Rich author details, version history and staff provisioning — M–L

- **Objective / deliverables:** Add role-safe, integrated author version/history views and, if needed for meetings, authorized staff provisioning. Build on existing author version endpoints and progress view.
- **Dependencies:** A3 privacy mapping; decide who can provision Reviewer/Editor/Formatter accounts and what fields (including title) are required.
- **Acceptance / tests:** Only permitted versions/history appear, downloads preserve filenames/bytes, staff roles cannot be self-granted, no exposed secrets or reviewer identity. Test account authorization and legacy/unknown history. **Schema:** possible additive staff/title metadata. **Approval:** staff authority and migration if necessary.

### B4. AI-01 → AI-02 → AI-03 advisory screening — L

- **Objective / deliverables:** Small privacy/version design, local isolated prototype for document structure, formatting and language, then EIC-only version-specific advisory report. Initiate after formal submission without gating EIC screening.
- **Dependencies:** Explicit processing/data-retention/model choice, synthetic fixtures, supported PDF/DOCX/DOC extraction. External AI use requires separate explicit approval; no assumptions about a real comparison corpus.
- **Acceptance / tests:** Report identifies the exact submitted version; decisions remain available when pending, failure or timeout; role/privacy isolation, no unapproved network, correct labels and no plagiarism claim. Test synthetic formats, unsupported/error/timeouts, version changes and decision availability. **Schema:** likely additive report/job state and version linkage; explicit migration approval. **Approval:** privacy/model approach, external services separately, and schema.

### B5. Remaining in-app notification coverage — M

- **Objective / deliverables:** Complete an event/recipient audit for submission, screening, invitations, reviews, decisions, agreements, final editing, formatting and publication; add only confirmed missing in-app notices.
- **Dependencies:** A6, approved recipient/identity privacy matrix, existing `NotificationBell.jsx` and `notifications.py`.
- **Acceptance / tests:** Correct recipients, read/unread, no unintended reviewer identity disclosure, one notice per successful event and transactional rollback. **Schema:** likely none for existing model. **Approval:** ambiguous recipient policies; no email activation.

## Priority C — Advanced or external integrations

### C1. Workflow email delivery and operational visibility — L

- **Objective / deliverables:** Only if later approved, connect existing disabled enqueue/outbox, claims, attempts, templates and verified-recipient checks through a reviewed fake-only orchestrator before any provider delivery.
- **Dependencies:** Owner decisions on recipients, consent, trusted origin, unknown outcomes and email verification; approved provider/configuration and per-environment migrations. Existing Resend verification transport is restricted to a configured test recipient and Demo-disabled; do not mistake it for general invitation delivery.
- **Acceptance / tests:** Mock-provider delivery, leases/fencing, cancellation and verification, crash/unknown outcomes, no automatic retry of unknown, no secrets; provider acceptance is not inbox delivery. **Schema:** 007/008 and perhaps future additions, separate approval. **Approval:** each integration and real delivery independently.

### C2. Google sign-in and CAPTCHA — L

- **Objective / deliverables:** Provider-backed OAuth and accessible bot protection, without altering role authority or linking accounts solely by an unverified claim.
- **Dependencies:** Provider account, callback/origin, account linking and CAPTCHA outage/accessibility policy.
- **Acceptance / tests:** Fake-provider state/nonce, redirects, account takeover, backend role dispatch, failure and accessibility; local demo fallback if approved. **Schema:** possible linked-identity storage. **Approval:** external setup, configuration, privacy and migration.

### C3. Real DOI, publication and similarity — L

- **Objective / deliverables:** Authorized publisher/DOI registration and, separately, evidence-based similarity comparison against an approved corpus or service.
- **Dependencies:** Contracts, permissions, privacy, funding and policy; no false plagiarism or registered-DOI claim.
- **Acceptance / tests:** Fake sandbox metadata, idempotency, failure/reconciliation, corpus provenance and qualified reporting. **Schema:** likely additive. **Approval:** external service/data sharing and migration.

### C4. Enterprise lifecycle and deployment hardening — L, deferred

- **Objective / deliverables:** Return to administrator lifecycle, JWT generation, audit and operational controls only if the course goal later requires them or a confirmed blocking defect arises.
- **Dependencies:** Owner policies and separately approved migrations; current prototype is disconnected.
- **Acceptance / tests:** Full authorization/race/legacy/restore suite and controlled deployment review. **Schema:** 009 and potential future grants. **Approval:** separate per environment and rollout. No automatic production integration.

## Final decisions and release gate

After A1/A2, implement A2a readability and A2b five-card login, then design and implement A2c multi-format uploads before completing A3–A6; A2a must be rechecked after the shared shell changes. Obtain decisions on exact publication-proof requirements, any preview/conversion dependency, safe original-filename metadata, logo, statistics, anonymity, split-review behavior, title fields and file-editing interpretation. The five-card layout and four supported upload formats are decided requirements, not open decisions. Before B1/B2 agree preliminary consent, recheck and accepted-participant rules. AI processing must remain advisory and exact-version bound; no external manuscript sharing without approval. Do not market DOI, publication, email, plagiarism or identity security beyond what the tests and actual integration prove.

The classroom release gate is a current synthetic end-to-end run with tested AA-targeted readable UI and focus states, five primary role cards, all four required formats at each applicable existing upload stage, correct immutable reviewer files, role separation, local prototype publication labeling and same-root restart verification. Follow up any real Demo inspection/backup/migration only after explicit authorization; never reset or recreate existing data to make the demo work.

## Tests and files

This planning step ran no tests, servers, API writes or migrations. Findings are from source inspection and historical reports only. Created only this roadmap and `reports/step_128_27_22_remaining_work_audit.md`; no source, master backlog, databases, uploads, credentials, configuration or external services changed. No commit, push or deployment occurred.
