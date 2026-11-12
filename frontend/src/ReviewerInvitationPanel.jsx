import { useCallback, useEffect, useState } from "react";
import { API_URL, actionFailureMessage, submitAction } from "./api";

export default function ReviewerInvitationPanel({ manuscriptId, round, status, token, onChanged }) {
  const [assignments, setAssignments] = useState([]);
  const [progress, setProgress] = useState(null);
  const [reviewerId, setReviewerId] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const load = useCallback(async () => {
    const headers = { Authorization: `Bearer ${token}` };
    const [response, summary] = await Promise.all([
      fetch(`${API_URL}/assignments/manuscript/${manuscriptId}`, { headers }),
      fetch(`${API_URL}/assignments/manuscript/${manuscriptId}/summary`, { headers }),
    ]);
    if (!response.ok || !summary.ok) throw new Error("Could not load reviewer invitations and progress.");
    setAssignments(await response.json());
    setProgress((await summary.json()).decision_progress);
  }, [manuscriptId, token]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  const invite = async event => {
    event.preventDefault(); if (busy || needsRefresh) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await submitAction(`${API_URL}/assignments/`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ manuscript_id: manuscriptId, reviewer_id: Number(reviewerId), review_round: round }) });
    } catch (e) {
      setError(actionFailureMessage(e)); if (!e.confirmedRejection) setNeedsRefresh(true); setBusy(false); return;
    }
    setReviewerId(""); setMessage("Reviewer invitation sent.");
    try { await load(); if (onChanged && await onChanged() === false) throw new Error("Refresh superseded"); }
    catch { setNeedsRefresh(true); setError("Invitation saved, but refresh failed. Displayed data may be stale. Refresh reviewer invitations; do not repeat the action."); }
    finally { setBusy(false); }
  };
  const current = assignments.filter(a => a.review_round === round);
  const active = current.filter(a => a.invitation_status !== "declined");
  return <section aria-label="Reviewer invitations"><h4>Reviewer invitations · round {round}</h4>
    <p>{active.length} active reviewer{active.length === 1 ? "" : "s"}.</p>
    {progress && <><p>Review progress: {Math.min(progress.completed_distinct, progress.required)}/{progress.required} completed distinct reviewers{progress.policy === "legacy" ? " (historical rule)" : ""}{progress.completed_distinct > progress.required ? `; ${progress.completed_distinct} total completed` : ""}.</p>{["ready_for_peer_review", "revised"].includes(status) && progress.blocked_reason && <p>Decision blocked: {progress.blocked_reason}</p>}</>}
    <button type="button" disabled={busy} onClick={async () => { try { await load(); if (onChanged && await onChanged() === false) throw new Error("Refresh superseded"); setError(""); setNeedsRefresh(false); } catch (e) { setError(e.message); } }}>Refresh reviewer invitations</button>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {assignments.length ? <ul>{assignments.map(a => <li key={a.assignment_id}>Round {a.review_round} · Reviewer #{a.reviewer_id} · invitation: {a.invitation_status === "legacy" ? "existing assignment" : a.invitation_status} · review: {a.status === "completed" ? "completed" : a.invitation_status === "declined" ? "declined" : a.invitation_status === "pending" ? "awaiting response" : "assigned"}</li>)}</ul> : <p>No reviewer invitations or assignments.</p>}
    {["ready_for_peer_review", "revised"].includes(status) && <form onSubmit={invite}><label>Reviewer ID <input type="number" min="1" required value={reviewerId} onChange={e => setReviewerId(e.target.value)} /></label> <button disabled={busy || needsRefresh}>Invite reviewer</button></form>}
  </section>;
}
