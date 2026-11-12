import { useCallback, useEffect, useState } from "react";
import { API_URL } from "./api";
import "./InitialEditingPanel.css";

const readable = value => (value || "unknown").replaceAll("_", " ");

function nextAction(item) {
  const submission = item.submissions[0];
  if (item.status === "initial_editor_invited") return "Invited editor: accept or decline the invitation.";
  if (item.status === "pre_review_confirmation") return "Author: confirm the EIC-approved pre-review PDF or request changes.";
  if (item.status === "ready_for_peer_review") return "Assigned editor: proceed with reviewer assignment.";
  if (item.status !== "initial_editing") return `Current manuscript status: ${readable(item.status)}.`;
  if (!submission || submission.status.endsWith("revision_requested") || submission.confirmation === "request_revision") return "Assigned editor: upload an initial-editing PDF.";
  if (submission.status === "pending_author") return "Author: review the initial-editing PDF.";
  if (submission.status === "author_approved") return "Assigned editor: review and approve the author's response.";
  if (submission.status === "editor_approved") return "EIC: review the initial-editing PDF.";
  return "Review the current submission and refresh for the latest status.";
}

function EditingItem({ item, role, perform, download, busy }) {
  const [decision, setDecision] = useState("");
  const [comments, setComments] = useState("");
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const current = item.submissions[0];
  const accepted = item.invitation?.status === "accepted";
  const needsUpload = !current || current.status.endsWith("revision_requested") || current.confirmation === "request_revision";
  const canUpload = role === "editor" && accepted && item.status === "initial_editing" && needsUpload;
  const review = role === "author" && item.status === "initial_editing" && current?.status === "pending_author";
  const editorApproval = role === "editor" && accepted && item.status === "initial_editing" && current?.status === "author_approved";
  const confirmation = role === "author" && item.status === "pre_review_confirmation" && current?.status === "eic_approved" && !current.confirmation;
  const submitDecision = async event => {
    event.preventDefault();
    const action = review ? "author-review" : editorApproval ? "editor-approval" : "pre-review-confirmation";
    if (await perform(`/eic/initial-editing/submissions/${current.submission_id}/${action}`, { decision, comments })) {
      setDecision(""); setComments("");
    }
  };
  const upload = async event => {
    event.preventDefault();
    const data = new FormData(); data.append("file", file); data.append("comments", comments);
    if (await perform(`/eic/initial-editing/${item.manuscript_id}/submit`, data)) {
      setComments(""); setFile(null); setFileKey(key => key + 1);
    }
  };
  return <article className="initial-editing-item">
    <h3>#{item.manuscript_id} {item.title}</h3>
    <p><strong>Manuscript status:</strong> {readable(item.status)}</p>
    {item.invitation && <p><strong>Initial editor:</strong> {item.invitation.editor} · {readable(item.invitation.status)}</p>}
    <p><strong>Next action:</strong> {nextAction(item)}</p>
    {role === "editor" && item.status === "initial_editor_invited" && item.invitation?.status === "pending" && <div className="initial-editing-buttons">
      <button type="button" disabled={busy} onClick={() => perform(`/eic/initial-editor/invitations/${item.invitation.invitation_id}/respond`, { decision: "accept" })}>Accept invitation</button>
      <button type="button" disabled={busy} onClick={() => perform(`/eic/initial-editor/invitations/${item.invitation.invitation_id}/respond`, { decision: "decline" })}>Decline invitation</button>
    </div>}
    {item.submissions.map(submission => <div className="initial-editing-submission" key={submission.submission_id}>
      <p><strong>Initial-editing PDF #{submission.submission_id}:</strong> {readable(submission.status)}{submission.confirmation && ` · author confirmation: ${readable(submission.confirmation)}`}</p>
      <p>Editor: {submission.comments}{submission.author_comments && ` · Author: ${submission.author_comments}`}{submission.editor_comments && ` · Editor approval: ${submission.editor_comments}`}{submission.eic_comments && ` · EIC: ${submission.eic_comments}`}</p>
      <button type="button" disabled={busy} onClick={() => download(submission.submission_id)}>Download initial-editing PDF</button>
    </div>)}
    {canUpload && <form onSubmit={upload} className="initial-editing-form"><h4>Upload initial-editing PDF</h4>
      <label>PDF file <input key={fileKey} type="file" accept=".pdf,application/pdf" required onChange={e => setFile(e.target.files[0] || null)} /></label>
      <label>Editing comments <textarea required maxLength={5000} value={comments} onChange={e => setComments(e.target.value)} /></label>
      <button disabled={busy || !file}>Submit initial-editing PDF</button></form>}
    {(review || editorApproval || confirmation) && <form onSubmit={submitDecision} className="initial-editing-form"><h4>{review ? "Review initial editing" : editorApproval ? "Editor approval" : "Confirm pre-review version"}</h4>
      <label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose decision</option>
        <option value={confirmation ? "confirm" : "approve"}>{confirmation ? "Confirm" : "Approve"}</option><option value="request_revision">Request revision</option></select></label>
      <label>Comments <textarea required maxLength={5000} value={comments} onChange={e => setComments(e.target.value)} /></label>
      <button disabled={busy}>Submit {confirmation ? "confirmation" : editorApproval ? "editor approval" : "author review"}</button></form>}
  </article>;
}

export default function InitialEditingPanel({ role, token, onChanged }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const response = await fetch(`${API_URL}/eic/initial-editing/my`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error(`Could not load initial editing (${response.status}).`);
    setItems(await response.json());
  }, [token]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  const perform = async (path, data) => {
    setBusy(true); setError(""); setMessage("");
    try {
      const isForm = data instanceof FormData;
      const response = await fetch(`${API_URL}${path}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, ...(isForm ? {} : { "Content-Type": "application/json" }) }, body: isForm ? data : JSON.stringify(data) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`);
      setMessage("Action completed.");
      try { await load(); await onChanged(); } catch { setError("Action saved, but refresh failed. Refresh this page before taking another action."); }
      return true;
    } catch (e) { setError(e.message); return false; } finally { setBusy(false); }
  };
  const download = async submissionId => {
    setError("");
    try {
      const response = await fetch(`${API_URL}/eic/initial-editing/submissions/${submissionId}/download`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Download failed (${response.status}).`); }
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const disposition = response.headers.get("content-disposition") || "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `initial-editing-${submissionId}.pdf`;
      const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e.message); }
  };
  return <section className="initial-editing-panel" aria-label="Initial editing workflow"><div className="initial-editing-heading"><h2>Initial editing workflow</h2><button type="button" disabled={busy} onClick={() => load().catch(e => setError(e.message))}>Refresh initial editing</button></div>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {!items.length ? <p>No initial editing items.</p> : items.map(item => <EditingItem key={item.manuscript_id} item={item} role={role} perform={perform} download={download} busy={busy} />)}
  </section>;
}
