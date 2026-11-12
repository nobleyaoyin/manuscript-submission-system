# Step 128.27.23 — Baseline Blockers and Gaps

## Confirmed test outcome

The final isolated continuous **API** scenario reached local publication and separately completed revision/round two; no blocking API failure was observed along the tested happy paths. A real headless browser verified the role landing page, Author login and manuscript visibility only. Full browser workflow, all roles, accessibility and deployment remain unverified. Synthetic restart checks passed as described in `step_128_27_23_demo_persistence.md`. This is not proof that the current real Demo is healthy.

## Ranked remaining work

| Rank | Finding | Evidence / reproduction | Next action / limitation |
|---|---|---|---|
| A1 | Full workflow not verified through all React dashboards | Current run drove workflow APIs and used the browser only for Author login; stage-specific existing browser tests were not rerun. | Add isolated browser coverage for all participants and exact filename/extension assertions; treat any actual failure separately. |
| A2 | Four primary role cards; Formatter only secondary | Browser observed Formatter sign-in link, source `frontend/src/RoleSelection.jsx` has four primary cards. | Owner-required fifth primary card, with existing backend authority and role mismatch tests; no implementation authorized in this baseline. |
| A3 | Universal PDF/DOCX/DOC/TXT support incomplete | Source `manuscripts.py` supports PDF/DOCX/DOC; `eic.py`, `formatting.py` and first-round `review_files.py` retain PDF-only checks. Current API run used PDF and later-round DOCX only. | Implement approved A2c as a separately scoped task after proof/version policy decisions; preserve historical round bindings. |
| A4 | Text readability and AA contrast not verified | No measured contrast or screenshot/accessibility audit was performed; code has mixed dashboard styles. | Perform the owner-required global computed-contrast, focus, light/dark and responsive checks in its own task. |
| A5 | Real Demo record disappearance remains unexplained | Synthetic same-root restart passed; real Demo and credentials were not inspected. | No real-data inference; reproduce with temporary setup or request separate explicit authorization for read-only diagnosis. |
| A6 | Reviewer directory, anonymity, 50/50 policy and progress/sidebar UX remain open | Prior audit found numeric reviewer ID selection, no configured anonymity mode and no split rule. | Follow approved roadmap; obtain product decisions for identity visibility, professional titles and split-review policy. |
| B | Preliminary editor consent and explicit recheck remain incomplete | Current path used existing invitation flow and new review-round assignment, not a separate consent/recheck step. | Agree workflow and history policy; do not conflate initial editor consent with post-acceptance agreement. |
| B | Agreement text is placeholder; real DOI unavailable | Current publication returned `pending_external`; source uses prototype agreement. | Clearly label demo artifacts; approve real terms and external integration separately. |
| B/C | AI screening, notification coverage and external integrations | This run did not exercise or enable AI, workflow email, Google, CAPTCHA or real DOI. | Preserve AI-01/02/03 and remaining B/C roadmap; external services require separate approval. |

## Test setup issues, not confirmed product defects

The temporary harness first duplicated synthetic account rows, then a sandbox denied localhost binding, then a synthetic reserved-domain address produced login validation 422. After fixing only the harness fixtures and using the required permission mechanism, the final run passed the stated API/browser/persistence checks. The existing isolated API regression suites passed 50 + 55 + 6 tests; SQLAlchemy emitted an existing `datetime.utcnow()` deprecation warning. No app code was modified.

## Recommended next step

Review the baseline and choose one scoped Priority A task. A global readability audit or the fifth role card is a small frontend step; multi-format upload requires a larger approved design because round-one confirmation, proof formats, immutable bindings and filenames are coupled. Do not claim the current baseline passed a full UI end-to-end workflow. No implementation is started by this report.
