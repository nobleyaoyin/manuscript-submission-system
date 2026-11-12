import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, actionFailureMessage, submitAction } from "./api";
import NotificationBell from "./NotificationBell";
import FormattingPanel from "./FormattingPanel";
import ReviewerInvitationPanel from "./ReviewerInvitationPanel";
import "./EICDashboard.css";

const label = value => (value || "unknown").replaceAll("_", " ");

export default function EICDashboard({ user, token, onLogout }) {
  const [manuscripts, setManuscripts] = useState([]);
  const [editors, setEditors] = useState([]);
  const [selected, setSelected] = useState(null);
  const [overview, setOverview] = useState(null);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [decision, setDecision] = useState("");
  const [comments, setComments] = useState("");
  const [editor, setEditor] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const actionLock = useRef(false);
  const selectedRef = useRef(null);
  const requestVersion = useRef(0);
  const request = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API_URL}${path}`, { ...options, headers: { Authorization: `Bearer ${token}`, ...options.headers } });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
    }
    return response.json();
  }, [token]);
  const refresh = useCallback(async (id = null) => {
    // A completed request for an older selection must never replace the current view.
    if (selectedRef.current !== id) return;
    const version = ++requestVersion.current;
    try {
      const [list, people, details] = await Promise.all([
        request("/manuscripts/"), request("/eic/editors"),
        id ? request(`/eic/manuscripts/${id}/overview`) : Promise.resolve(null),
      ]);
      if (version !== requestVersion.current || selectedRef.current !== id) return false;
      setManuscripts(list); setEditors(people);
      if (id) setOverview(details);
      return true;
    } catch (e) {
      if (version !== requestVersion.current || selectedRef.current !== id) return false;
      throw e;
    }
  }, [request]);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
  useEffect(() => {
    const onFocus = () => {
      if (!busy && document.visibilityState === "visible") {
        refresh(selected).then(updated => { if (updated) { setError(""); setNeedsRefresh(false); } }).catch(e => setError(`Refresh failed: ${e.message}`));
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [busy, refresh, selected]);
  const refreshView = async () => {
    setError("");
    try { if (await refresh(selected)) setNeedsRefresh(false); } catch (e) { setError(`Refresh failed: ${e.message}`); }
  };
  const open = async id => {
    selectedRef.current = id;
    const version = ++requestVersion.current;
    setSelected(id); setOverview(null); setDecision(""); setComments("");
    if (!needsRefresh) { setError(""); setMessage(""); }
    try {
      const details = await request(`/eic/manuscripts/${id}/overview`);
      if (version === requestVersion.current && selectedRef.current === id) setOverview(details);
    } catch (e) {
      if (version === requestVersion.current && selectedRef.current === id) setError(e.message);
    }
  };
  const act = async (path, payload) => {
    if (actionLock.current || needsRefresh) return;
    actionLock.current = true; setBusy(true); setError(""); setMessage("");
    try {
      await submitAction(`${API_URL}${path}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    } catch (e) {
      setError(actionFailureMessage(e));
      if (!e.confirmedRejection) setNeedsRefresh(true);
      actionLock.current = false; setBusy(false);
      return;
    }
    // A successful HTTP response confirms the write even if a later GET fails.
    setMessage("Action completed."); setDecision(""); setComments(""); setEditor("");
    try {
      if (!await refresh(selectedRef.current)) throw new Error("Refresh superseded");
    } catch {
      setNeedsRefresh(true);
      setError("Action saved, but dashboard refresh failed. Displayed data may be stale. Refresh the dashboard; do not repeat the action.");
    } finally { actionLock.current = false; setBusy(false); }
  };
  const download = async path => {
    try {
      const response = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Download failed (${response.status})`); }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || "manuscript";
      const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e.message); }
  };
  const visible = manuscripts.filter(m => (!filter || m.status === filter) && (`${m.title} ${m.manuscript_id}`.toLowerCase().includes(search.toLowerCase())));
  const status = overview?.manuscript.status;
  const round = overview?.manuscript.review_round;
  const canDecide = ["ready_for_peer_review", "revised"].includes(status) && overview.review_progress.can_decide && !overview.decisions.some(d => d.review_round === round);
  const approval = overview?.submissions.filter(s => s.status === "editor_approved") || [];
  const activeFinal = overview?.final_invitations.find(i => ["pending", "accepted"].includes(i.status));
  const latestFinal = overview?.final_workflow_submissions[0];
  return <div className="eic-page">
    <header><div><h1>EIC Dashboard</h1><p>Welcome, {user.name}</p></div><div><NotificationBell token={token} /><button onClick={onLogout}>Logout</button></div></header>
    {error && <p role="alert" className="eic-error">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="eic-grid"><section><h2>Manuscripts</h2><button type="button" disabled={busy} onClick={refreshView}>Refresh dashboard</button><label>Search <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Title or ID" /></label>
      <label>Status <select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All statuses</option>{[...new Set(manuscripts.map(m => m.status))].sort().map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      {!visible.length && <p>No manuscripts found.</p>}<ul className="eic-list">{visible.map(m => <li key={m.manuscript_id}><button onClick={() => open(m.manuscript_id)} aria-pressed={selected === m.manuscript_id}>#{m.manuscript_id} {m.title}<br /><small>{label(m.status)}</small></button></li>)}</ul></section>
    <main>{!selected && <p>Select a manuscript to see its workflow and actions.</p>}{selected && !overview && <p>Loading manuscript details…</p>}{overview && <>
      <h2>#{selected} {overview.manuscript.title}</h2><p><strong>Status:</strong> {label(status)} · <strong>Review round:</strong> {round} · <strong>Author:</strong> {overview.manuscript.author}</p><p>{overview.manuscript.abstract}</p>
      <button onClick={() => download(`/manuscripts/${selected}/download`)}>Download submitted manuscript</button>
      <h3>Next actions</h3>
      {status === "final_editing" && overview.author_agreement?.status === "accepted" && !activeFinal && <form onSubmit={e => { e.preventDefault(); act('/final-workflow/invite', { manuscript_id: selected, editor_id: Number(editor) }); }}><h4>Invite final editor</h4><label>Editor <select required value={editor} onChange={e => setEditor(e.target.value)}><option value="">Choose editor</option>{editors.map(x => <option value={x.user_id} key={x.user_id}>{x.name}</option>)}</select></label><button disabled={busy || needsRefresh}>Send final editor invitation</button></form>}
      {status === "final_editing" && latestFinal?.status === "pending_eic" && <form onSubmit={e => { e.preventDefault(); act(`/final-workflow/submissions/${latestFinal.submission_id}/eic-review`, { decision, comments }); }}><h4>Review final edit #{latestFinal.submission_id}</h4><label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose</option><option value="approve">Approve</option><option value="request_revision">Request corrections</option></select></label><label>Comments <textarea required maxLength="5000" value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy || needsRefresh}>Submit final edit review</button></form>}
      {["ready_for_peer_review", "revised"].includes(status) && <p>Review progress: {Math.min(overview.review_progress.completed_distinct, overview.review_progress.required)}/{overview.review_progress.required} completed distinct reviewers{overview.review_progress.policy === "legacy" ? " (historical rule)" : ""}. {overview.review_progress.blocked_reason && `Decision blocked: ${overview.review_progress.blocked_reason}`}</p>}
      {status === "submitted" && <form onSubmit={e => { e.preventDefault(); act(`/eic/manuscripts/${selected}/initial-scan`, { decision, comments }); }}><h4>Initial screening</h4><label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose</option><option value="pass">Pass</option><option value="revision_required">Request revision</option><option value="reject">Reject</option></select></label><label>Comments <textarea required maxLength="5000" value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy || needsRefresh}>Record screening</button></form>}
      {status === "initial_scan_passed" && <form onSubmit={e => { e.preventDefault(); act("/eic/initial-editor/invite", { manuscript_id: selected, editor_id: Number(editor) }); }}><h4>Invite initial editor</h4><label>Editor <select required value={editor} onChange={e => setEditor(e.target.value)}><option value="">Choose editor</option>{editors.map(x => <option value={x.user_id} key={x.user_id}>{x.name}</option>)}</select></label><button disabled={busy || needsRefresh}>Send invitation</button></form>}
      {status === "initial_editing" && approval.map(s => <form key={s.submission_id} onSubmit={e => { e.preventDefault(); act(`/eic/initial-editing/submissions/${s.submission_id}/eic-approval`, { decision, comments }); }}><h4>Review initial editing submission #{s.submission_id}</h4><label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose</option><option value="approve">Approve</option><option value="request_revision">Request revision</option></select></label><label>Comments <textarea required maxLength="5000" value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy || needsRefresh}>Record editing decision</button></form>)}
      {canDecide && <form onSubmit={e => { e.preventDefault(); act(`/eic/manuscripts/${selected}/decision`, { decision, comments }); }}><h4>Editorial decision for round {round}</h4><label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose</option><option value="accept">Accept</option><option value="revision_required">Request revision</option><option value="reject">Reject</option></select></label><label>Comments <textarea required maxLength="5000" value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy || needsRefresh}>Record editorial decision</button></form>}
      {!(["submitted", "initial_scan_passed", "final_editing"].includes(status) || (status === "initial_editing" && approval.length) || canDecide) && <p>{status === "initial_editor_invited" ? "Next: invited editor accepts or declines." : status === "pre_review_confirmation" ? "Next: author confirms the EIC-approved pre-review PDF." : status === "awaiting_author_agreement" ? "Next: author responds to the prototype agreement." : status === "author_agreement_declined" ? "The author declined; final editing is on hold." : status === "initial_editing" ? (() => { const latest = overview.submissions.at(-1); const confirmation = overview.confirmations.find(c => c.submission_id === latest?.submission_id); if (!latest || latest.status.endsWith("revision_requested") || confirmation?.decision === "request_revision") return "Next: assigned editor submits an initial-editing PDF."; if (latest.status === "pending_author") return "Next: author reviews the initial-editing PDF."; if (latest.status === "author_approved") return "Next: assigned editor records approval."; return "Review the latest submission and confirmation for the next action."; })() : "No EIC action is currently available. Reviewer assignment is performed by the assigned editor; pending reviews or other participants may need to act."}</p>}
      <h3>Initial screening history</h3>{overview.scans.length ? overview.scans.map((s, i) => <p key={i}>{label(s.decision)} — {s.comments}</p>) : <p>No scans recorded.</p>}
      <h3>Initial editors</h3>{overview.invitations.length ? overview.invitations.map(i => <p key={i.invitation_id}>{i.editor} — {label(i.status)}</p>) : <p>No invitations.</p>}
      <h3>Initial editing submissions</h3>{overview.submissions.length ? overview.submissions.map(s => <div key={s.submission_id}><p>#{s.submission_id} · {s.editor} · {label(s.status)} — {s.comments}</p><p>Author: {s.author_comments || "—"}; Editor: {s.editor_comments || "—"}; EIC: {s.eic_comments || "—"}</p><button onClick={() => download(`/eic/initial-editing/submissions/${s.submission_id}/download`)}>Download initial editing PDF</button></div>) : <p>No initial editing submissions.</p>}
      <h3>Author pre-review confirmations</h3>{overview.confirmations.length ? overview.confirmations.map((c, i) => <p key={i}>Submission #{c.submission_id}: {label(c.decision)} — {c.comments}</p>) : <p>No confirmations.</p>}
      <ReviewerInvitationPanel key={selected} manuscriptId={selected} round={round} status={status} token={token} onChanged={() => refresh(selected)} />
      <h3>Reviewer assignments and results</h3>{overview.assignments.length ? overview.assignments.map(a => <div key={a.assignment_id}><p>Round {a.review_round} · {a.reviewer} · invitation: {label(a.invitation_status)} · review: {label(a.status)} · Assigned by {a.assigned_by}</p>{a.review && <p>Recommendation: {label(a.review.recommendation)} — {a.review.comments}</p>}</div>) : <p>No reviewers assigned.</p>}
      <h3>Editorial decision history</h3>{overview.decisions.length ? overview.decisions.map((d, i) => <p key={i}>Round {d.review_round}: {label(d.decision)} — {d.comments}</p>) : <p>No decisions recorded.</p>}
      <h3>Author agreement</h3>{overview.author_agreement ? <p>{label(overview.author_agreement.status)} · version {overview.author_agreement.agreement_version}{overview.author_agreement.responded_at ? ` · responded ${new Date(overview.author_agreement.responded_at).toLocaleString()}` : ""}{overview.author_agreement.status !== "accepted" ? " · final editing is on hold" : ""}</p> : <p>No agreement recorded.</p>}
      <h3>Final editor invitations</h3>{overview.final_invitations.length ? overview.final_invitations.map(i => <p key={i.invitation_id}>{i.editor} — {label(i.status)}</p>) : <p>No final editor invitation.</p>}
      {status === "final_editing" && activeFinal && <p>Next: {activeFinal.status === "pending" ? "final editor responds to the invitation" : !latestFinal || ["eic_revision_requested", "author_revision_requested"].includes(latestFinal.status) ? "final editor uploads the final PDF" : latestFinal.status === "pending_eic" ? "EIC reviews the final PDF" : latestFinal.status === "pending_author" ? "author reviews the EIC-approved PDF" : "final edits confirmed; invite a formatter"}.</p>}
      <FormattingPanel key={`format-${selected}`} role="eic" token={token} manuscriptId={selected} canInvite={latestFinal?.status === "author_approved"} />
      <h3>Final editing</h3>{overview.final_submissions.length ? overview.final_submissions.map(f => <div key={f.submission_id}><p>#{f.submission_id} · {f.editor} · {label(f.status)} — {f.comments}</p><button onClick={() => download(`/eic/final-editing/submissions/${f.submission_id}/download`)}>Download final editing PDF</button></div>) : <p>No final editing submissions.</p>}
    </>}</main></div></div>;
}
