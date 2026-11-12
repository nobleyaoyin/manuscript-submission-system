import { useCallback, useEffect, useState } from "react";
import { API_URL } from "./api";
import "./InitialEditingPanel.css";

export default function AuthorAgreementPanel({ token, onChanged }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const response = await fetch(`${API_URL}/agreements/my`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error("Could not load author agreements.");
    setItems(await response.json());
  }, [token]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  const respond = async (item, decision) => {
    setBusy(true); setError(""); setMessage("");
    let body;
    try {
      const response = await fetch(`${API_URL}/agreements/${item.agreement_id}/respond`, {
        method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ decision, agreement_version: item.agreement_version }),
      });
      body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`);
    } catch (e) {
      setError(`Could not confirm the agreement response: ${e.message}. Refresh agreements to check its status before trying again.`);
      setBusy(false);
      return;
    }
    // Keep the saved response visible and prevent another submission even if a refresh fails.
    const savedStatus = body.status || (decision === "accept" ? "accepted" : "declined");
    setItems(previous => previous.map(existing => existing.agreement_id === item.agreement_id
      ? { ...existing, status: savedStatus, manuscript_status: body.manuscript_status, responded_at: body.responded_at }
      : existing));
    setMessage(`Prototype agreement ${savedStatus}.`);
    const results = await Promise.allSettled([load(), Promise.resolve().then(() => onChanged())]);
    if (results.some(result => result.status === "rejected" || result.value === false)) {
      setError("Response saved, but refresh failed. Refresh this page to see the latest status; do not submit the response again.");
    }
    setBusy(false);
  };
  return <section className="initial-editing-panel" aria-label="Author agreements">
    <div className="initial-editing-heading"><h2>Author agreement</h2><button type="button" disabled={busy} onClick={() => load().catch(e => setError(e.message))}>Refresh agreements</button></div>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {!items.length ? <p>No author agreements.</p> : items.map(item => <article className="initial-editing-item" key={item.agreement_id}>
      <h3>#{item.manuscript_id} {item.manuscript_title}</h3>
      <p><strong>Agreement version:</strong> {item.agreement_version} · <strong>Status:</strong> {item.status}</p>
      <p>{item.agreement_text}</p>
      {item.responded_at && <p>Responded: {new Date(item.responded_at).toLocaleString()}</p>}
      {item.status === "pending" && item.manuscript_status === "awaiting_author_agreement" && <div className="initial-editing-buttons">
        <button type="button" disabled={busy} onClick={() => respond(item, "accept")}>Accept prototype agreement</button>
        <button type="button" disabled={busy} onClick={() => respond(item, "decline")}>Decline prototype agreement</button>
      </div>}
      {item.status === "declined" && <p>Final editing is on hold. Contact the editorial team.</p>}
    </article>)}
  </section>;
}
