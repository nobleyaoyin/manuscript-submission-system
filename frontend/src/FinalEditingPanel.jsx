import { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from "react";
import { API_URL } from "./api";
import "./InitialEditingPanel.css";

const readable = value => (value || "unknown").replaceAll("_", " ");

function FinalItem({ item, role, busy, perform, download }) {
  const [comments, setComments] = useState("");
  const [decision, setDecision] = useState("");
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const latest = item.submissions[0];
  const upload = async e => {
    e.preventDefault(); const data = new FormData(); data.append("file", file); data.append("comments", comments);
    if (await perform(`/eic/final-editing/${item.manuscript_id}/submit`, data)) { setComments(""); setFile(null); setFileKey(x => x + 1); }
  };
  const confirm = async e => {
    e.preventDefault(); if (await perform(`/final-workflow/submissions/${latest.submission_id}/author-review`, { decision, comments })) { setComments(""); setDecision(""); }
  };
  const next = role === "author" && !latest && item.final_invitation_status !== "accepted" ? (item.final_invitation_status === "pending" ? "Final editor: respond to the invitation." : "EIC: invite a final editor.") : role === "editor" && item.status === "pending" ? "Final editor: accept or decline the invitation." : role === "editor" && item.status === "declined" ? "EIC: invite a replacement." : !latest || ["eic_revision_requested", "author_revision_requested"].includes(latest.status) ? "Final editor: upload a PDF." : latest.status === "pending_eic" ? "EIC: review the final PDF." : latest.status === "pending_author" ? "Author: review the final PDF." : latest.status === "author_approved" ? "Final edits confirmed; EIC may invite a formatter." : "Review the current status.";
  return <article className="initial-editing-item"><h3>#{item.manuscript_id} {item.title}</h3><p>Manuscript status: {readable(item.manuscript_status)}</p>
    {role === "editor" && <p>Final editor invitation: {readable(item.status)}</p>}<p><strong>Next action:</strong> {next}</p>
    {role === "editor" && item.status === "pending" && <div><button type="button" disabled={busy} onClick={() => perform(`/final-workflow/invitations/${item.invitation_id}/respond`, { decision: "accept" })}>Accept final editor invitation</button> <button type="button" disabled={busy} onClick={() => perform(`/final-workflow/invitations/${item.invitation_id}/respond`, { decision: "decline" })}>Decline final editor invitation</button></div>}
    {item.submissions.map(s => <div key={s.submission_id} className="initial-editing-submission"><p>Final edit #{s.submission_id} · {readable(s.status)} — {s.comments}</p>{s.eic_comments && <p>EIC: {s.eic_comments}</p>}{s.author_comments && <p>Author: {s.author_comments}</p>}<button type="button" disabled={busy} onClick={() => download(s.submission_id)}>Download final editing PDF</button></div>)}
    {role === "editor" && item.status === "accepted" && item.manuscript_status === "final_editing" && (!latest || ["eic_revision_requested", "author_revision_requested"].includes(latest.status)) && <form className="initial-editing-form" onSubmit={upload}><h4>Upload final editing PDF</h4><label>PDF file <input key={fileKey} type="file" accept=".pdf,application/pdf" required onChange={e => setFile(e.target.files[0] || null)} /></label><label>Editing comments <textarea required maxLength={5000} value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy || !file}>Submit final editing PDF</button></form>}
    {role === "author" && item.manuscript_status === "final_editing" && latest?.status === "pending_author" && <form className="initial-editing-form" onSubmit={confirm}><h4>Review final edits</h4><label>Decision <select required value={decision} onChange={e => setDecision(e.target.value)}><option value="">Choose</option><option value="approve">Approve final edits</option><option value="request_revision">Request corrections</option></select></label><label>Comments <textarea required maxLength={5000} value={comments} onChange={e => setComments(e.target.value)} /></label><button disabled={busy}>Submit final edit confirmation</button></form>}
  </article>;
}

const FinalEditingPanel = forwardRef(function FinalEditingPanel({ role, token, onChanged }, ref) {
  const [items, setItems] = useState([]); const [error, setError] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const response = await fetch(`${API_URL}/final-workflow/my`, { headers: { Authorization: `Bearer ${token}` } }); if (!response.ok) throw new Error(`Could not load final editing (${response.status}).`); setItems(await response.json()); }, [token]);
  useImperativeHandle(ref, () => ({ refresh: load }), [load]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  const perform = async (path, data) => {
    setBusy(true); setError(""); setMessage("");
    try { const form = data instanceof FormData; const response = await fetch(`${API_URL}${path}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, ...(form ? {} : { "Content-Type": "application/json" }) }, body: form ? data : JSON.stringify(data) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`); setMessage("Action completed."); try { await load(); await onChanged(); } catch { setError("Action saved, but refresh failed. Refresh this page."); } return true; } catch (e) { setError(e.message); return false; } finally { setBusy(false); }
  };
  const download = async id => { try { const response = await fetch(`${API_URL}/eic/final-editing/submissions/${id}/download`, { headers: { Authorization: `Bearer ${token}` } }); if (!response.ok) throw new Error("Final edit is unavailable."); const blob = await response.blob(); const url = URL.createObjectURL(blob); const disposition = response.headers.get("content-disposition") || ""; const a = document.createElement("a"); a.href = url; a.download = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `final-edit-${id}.pdf`; a.click(); URL.revokeObjectURL(url); } catch (e) { setError(e.message); } };
  return <section className="initial-editing-panel" aria-label="Final editing workflow"><div className="initial-editing-heading"><h2>Final editing workflow</h2><button type="button" disabled={busy} onClick={() => load().catch(e => setError(e.message))}>Refresh final editing</button></div>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}{items.length ? items.map(item => <FinalItem key={item.invitation_id || item.manuscript_id} item={item} role={role} busy={busy} perform={perform} download={download} />) : <p>No final editing items.</p>}</section>;
});

export default FinalEditingPanel;
