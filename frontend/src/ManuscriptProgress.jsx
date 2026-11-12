import { useEffect, useState } from "react";
import { API_URL } from "./api";
import "./ManuscriptProgress.css";

const stages = ["Submission", "Initial Screening", "Initial Editing", "Peer Review", "EIC Decision", "Author Agreement", "Final Editing", "Formatting", "Publication"];

// Status indicates the present state, not reliable proof of earlier events.
// Legacy and branching statuses are deliberately left unmapped.
const current = {
  draft: ["Submission", "Upload a manuscript file and submit the draft when ready."],
  submitted: ["Initial Screening", "The EIC will perform initial screening."],
  initial_revision_required: ["Initial Screening", "Revise and resubmit the manuscript for initial screening."],
  initial_rejected: ["Initial Screening", "Initial screening rejected this manuscript."],
  initial_scan_passed: ["Initial Editing", "The EIC can select an initial editor."],
  initial_editor_invited: ["Initial Editing", "The invited editor must respond."],
  initial_editing: ["Initial Editing", "Check the Initial Editing panel for the next action."],
  pre_review_confirmation: ["Initial Editing", "Review and confirm the pre-review manuscript in the Initial Editing panel."],
  ready_for_peer_review: ["Peer Review", "The editorial team can arrange peer review."],
  revised: ["Peer Review", "The editorial team can arrange review of the revision."],
  awaiting_author_agreement: ["Author Agreement", "Review and respond in the Author Agreement panel."],
  author_agreement_declined: ["Author Agreement", "The agreement was declined; contact the editorial team about next steps."],
  final_editing: ["Final Editing", "Check the Final Editing panel for the next action."],
  formatting: ["Formatting", "Check the Formatting panel for the next action."],
  published: ["Publication", "A publication status has been recorded; consult the Formatting panel for details."],
};

function ManuscriptProgress({ manuscriptId, token }) {
  const [result, setResult] = useState({ loading: true });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setResult({ loading: true });
      try {
        const response = await fetch(`${API_URL}/manuscripts/${manuscriptId}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Unable to load manuscript progress.");
        const data = await response.json();
        if (!data || data.manuscript_id !== manuscriptId) throw new Error("Invalid manuscript response.");
        if (!controller.signal.aborted) setResult({ data });
      } catch {
        if (!controller.signal.aborted) setResult({ error: true });
      }
    }
    load();
    return () => controller.abort();
  }, [manuscriptId, token, attempt]);

  const data = result.data;
  const status = typeof data?.status === "string" && data.status.trim() ? data.status : null;
  const description = status ? current[status] : null;
  const stage = description?.[0];
  const action = description?.[1] || (status === "revision_required" ? "Revise and resubmit the manuscript using the author dashboard." : "Unavailable — consult the editorial team or the workflow panels.");
  const round = Number.isInteger(data?.review_round) && data.review_round > 0 ? data.review_round : "Unavailable";

  return (
    <section className="manuscript-progress" aria-label={`Manuscript ${manuscriptId} progress`}>
      <div className="progress-header">
        <h4>Manuscript Progress</h4>
        <button type="button" className="download-button" onClick={() => setAttempt(value => value + 1)} disabled={result.loading}>Refresh progress</button>
      </div>
      {result.loading ? <p role="status">Loading progress...</p> : result.error ? (
        <p role="alert">Unable to load manuscript progress. Refresh progress to try again.</p>
      ) : (
        <>
          <p><strong>Manuscript:</strong> {typeof data.title === "string" && data.title.trim() ? data.title : "Unavailable"} (#{manuscriptId})</p>
          <p><strong>Current status:</strong> {status ? status.replaceAll("_", " ") : "Unknown"}</p>
          <p><strong>Review round:</strong> {round}</p>
          <p><strong>Current stage:</strong> {stage || "Unknown"}</p>
          <p><strong>Next action:</strong> {action}</p>
          <p className="progress-note">Earlier stages are unknown unless supported by available evidence. Publication is a local prototype.</p>
          <ol className="progress-timeline" aria-label="Workflow timeline">
            {stages.map(name => {
              let state = "Unknown";
              if (name === stage) state = "Current";
              else if (status === "draft") state = "Pending";
              else if (status === "submitted" && name === "Submission") state = "Completed";
              return <li key={name} className={`progress-${state.toLowerCase()}`}><span>{name}</span><strong>{state}</strong></li>;
            })}
          </ol>
        </>
      )}
    </section>
  );
}

export default ManuscriptProgress;
