import FormattingPanel from "./FormattingPanel";

import { useCallback, useEffect, useRef, useState } from "react";
import "./AuthorDashboard.css";
import NotificationBell from "./NotificationBell";
import InitialEditingPanel from "./InitialEditingPanel";
import AuthorAgreementPanel from "./AuthorAgreementPanel";
import FinalEditingPanel from "./FinalEditingPanel";
import ManuscriptProgress from "./ManuscriptProgress";

import { API_URL } from "./api";
const MAX_FILE_SIZE = 10 * 1024 * 1024;

function AuthorDashboard({ user, token, onLogout }) {
  const [manuscripts, setManuscripts] = useState([]);
  const [progressId, setProgressId] = useState(null);

  const [title, setTitle] = useState("");
  const [abstract, setAbstract] = useState("");
  const [showForm, setShowForm] = useState(false);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [uploadingId, setUploadingId] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  // Revision form state
  const [editingId, setEditingId] = useState(null);
  const [revisionTitle, setRevisionTitle] = useState("");
  const [revisionAbstract, setRevisionAbstract] = useState("");
  const [revisionFile, setRevisionFile] = useState(null);
  const [resubmitting, setResubmitting] = useState(false);

  // Draft editing and final submission
  const [draftEditingId, setDraftEditingId] = useState(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftAbstract, setDraftAbstract] = useState("");
  const [savingDraft, setSavingDraft] = useState(false);
  const [submittingDraftId, setSubmittingDraftId] = useState(null);

  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const finalEditingPanel = useRef(null);
  const fileInputs = useRef({});
  const revisionFileInput = useRef(null);

  // Read API responses and handle errors
  const readResponse = async (response, fallback) => {
    const contentType =
      response.headers.get("content-type") || "";

    let data = null;

    if (contentType.includes("application/json")) {
      data = await response.json();
    } else {
      const text = await response.text();
      data = { detail: text || fallback };
    }

    if (!response.ok) {
      const detail = data?.detail;

      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
            : fallback
      );
    }

    return data;
  };

  // Load manuscripts
  const loadManuscripts = useCallback(async () => {
    setLoading(true);

    try {
      const response = await fetch(`${API_URL}/manuscripts/my`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const contentType =
        response.headers.get("content-type") || "";

      const data = contentType.includes("application/json")
        ? await response.json()
        : null;

      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : "Failed to load manuscripts"
        );
      }

      if (!Array.isArray(data)) {
        throw new Error("Invalid manuscript data received.");
      }

      setManuscripts(data);
      return true;
    } catch (err) {
      setManuscripts([]);
      setError(err.message || "Failed to load manuscripts.");
      return false;
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadManuscripts();
  }, [loadManuscripts]);

  const refreshAfterAgreement = async () => {
    const results = await Promise.allSettled([
      loadManuscripts(),
      finalEditingPanel.current?.refresh() ?? Promise.reject(new Error("Final editing panel unavailable")),
    ]);
    if (results.some(result => result.status === "rejected" || result.value === false)) {
      throw new Error("Could not refresh all panels");
    }
  };

  // Create manuscript
  const handleSubmit = async (event) => {
    event.preventDefault();

    if (submitting) return;

    if (!title.trim() || !abstract.trim()) {
      setError("Title and abstract cannot be empty.");
      return;
    }

    setSubmitting(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(`${API_URL}/manuscripts/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          title: title.trim(),
          abstract: abstract.trim(),
        }),
      });

      await readResponse(response, "Failed to save draft");

      setTitle("");
      setAbstract("");
      setShowForm(false);

      await loadManuscripts();

      setMessage("Draft saved successfully. Upload a PDF or Word file before submitting it.");
    } catch (err) {
      setError(err.message || "Failed to save draft.");
    } finally {
      setSubmitting(false);
    }
  };

  // Edit a saved draft without submitting it
  const startDraftEdit = (manuscript) => {
    setDraftEditingId(manuscript.manuscript_id);
    setDraftTitle(manuscript.title);
    setDraftAbstract(manuscript.abstract);
    setError("");
    setMessage("");
  };

  const cancelDraftEdit = () => {
    setDraftEditingId(null);
    setDraftTitle("");
    setDraftAbstract("");
  };

  const handleSaveDraft = async (event, manuscriptId) => {
    event.preventDefault();
    if (savingDraft) return;
    if (!draftTitle.trim() || !draftAbstract.trim()) {
      setError("Title and abstract cannot be empty.");
      return;
    }
    setSavingDraft(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API_URL}/manuscripts/${manuscriptId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          title: draftTitle.trim(),
          abstract: draftAbstract.trim(),
        }),
      });
      await readResponse(response, "Failed to update draft");
      cancelDraftEdit();
      const refreshed = await loadManuscripts();
      setMessage(refreshed
        ? `Draft #${manuscriptId} updated successfully.`
        : `Draft #${manuscriptId} updated, but the list could not be refreshed.`);
    } catch (err) {
      setError(err.message || "Failed to update draft.");
    } finally {
      setSavingDraft(false);
    }
  };

  const handleSubmitDraft = async (manuscript) => {
    const manuscriptId = manuscript.manuscript_id;
    if (submittingDraftId !== null || uploadingId !== null || savingDraft) return;
    if (!manuscript.has_pdf) {
      setError("Upload a PDF or Word file before submitting this draft.");
      return;
    }
    if (!window.confirm(`Submit Manuscript #${manuscriptId} for review? You will no longer be able to edit it as a draft.`)) return;
    setSubmittingDraftId(manuscriptId);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API_URL}/manuscripts/${manuscriptId}/submit`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      await readResponse(response, "Failed to submit manuscript");
      if (draftEditingId === manuscriptId) cancelDraftEdit();
      const refreshed = await loadManuscripts();
      setMessage(refreshed
        ? `Manuscript #${manuscriptId} submitted successfully!`
        : `Manuscript #${manuscriptId} submitted, but the list could not be refreshed.`);
    } catch (err) {
      setError(err.message || "Failed to submit manuscript.");
    } finally {
      setSubmittingDraftId(null);
    }
  };

  // Validate supported manuscript formats (PDF, DOCX, DOC)
  const validateManuscriptFile = (file) => {
    if (!file) {
      throw new Error("Please select a PDF, DOCX, or DOC file.");
    }

    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (![".pdf", ".docx", ".doc"].includes(extension)) {
      throw new Error("Only PDF, DOCX, and DOC files are allowed.");
    }

    if (file.size === 0) {
      throw new Error("The manuscript file is empty.");
    }

    if (file.size > MAX_FILE_SIZE) {
      throw new Error("Manuscript file exceeds the 10 MB limit.");
    }
  };

  // Shared manuscript upload request
  const uploadManuscriptFile = async (manuscriptId, file) => {
    validateManuscriptFile(file);

    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(
      `${API_URL}/manuscripts/${manuscriptId}/upload`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      }
    );

    return await readResponse(response, "Manuscript file upload failed");
  };

  // Upload manuscript file for draft
  const handleUpload = async (manuscriptId, file) => {
    if (!file || uploadingId !== null) return;

    setError("");
    setMessage("");
    setUploadingId(manuscriptId);

    try {
      await uploadManuscriptFile(manuscriptId, file);
      const refreshed = await loadManuscripts();
      setMessage(
        refreshed
          ? `Manuscript file uploaded successfully for Manuscript #${manuscriptId}.`
          : `Manuscript file uploaded for Manuscript #${manuscriptId}, but the manuscript list could not be refreshed.`
      );
    } catch (err) {
      setError(err.message || "Manuscript file upload failed.");
    } finally {
      setUploadingId(null);

      if (fileInputs.current[manuscriptId]) {
        fileInputs.current[manuscriptId].value = "";
      }
    }
  };

  // Download manuscript in its original format
  const handleDownload = async (manuscriptId) => {
    if (downloadingId !== null) return;

    setError("");
    setMessage("");
    setDownloadingId(manuscriptId);

    try {
      const response = await fetch(
        `${API_URL}/manuscripts/${manuscriptId}/download`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!response.ok) {
        await readResponse(response, "Manuscript download failed");
        return;
      }

      const blob = await response.blob();
      if (blob.size === 0) {
        throw new Error("Downloaded manuscript file is empty.");
      }
      const url = URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = url;
      const contentType = (response.headers.get("content-type") || "")
        .split(";")[0].trim().toLowerCase();
      const extensionsByMime = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
      };
      const disposition = response.headers.get("content-disposition") || "";
      const filenameMatch = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i);
      const headerFilename = filenameMatch?.[1]?.trim();
      const headerExtension = headerFilename?.match(/\.(pdf|docx|doc)$/i)?.[0]?.toLowerCase();
      const extension = headerExtension || extensionsByMime[contentType];
      if (!extension) {
        throw new Error("Unsupported manuscript download format.");
      }
      link.download = `manuscript_${manuscriptId}${extension}`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err.message || "Manuscript download failed.");
    } finally {
      setDownloadingId(null);
    }
  };

  // Open revision form
  const startRevision = (manuscript) => {
    setEditingId(manuscript.manuscript_id);
    setRevisionTitle(manuscript.title);
    setRevisionAbstract(manuscript.abstract);
    setRevisionFile(null);

    setError("");
    setMessage("");
  };

  // Close revision form
  const cancelRevision = () => {
    setEditingId(null);
    setRevisionTitle("");
    setRevisionAbstract("");
    setRevisionFile(null);

    if (revisionFileInput.current) {
      revisionFileInput.current.value = "";
    }
  };

  // Resubmit revised manuscript
  const handleResubmit = async (event, manuscriptId) => {
    event.preventDefault();

    if (resubmitting) return;

    if (!revisionTitle.trim() || !revisionAbstract.trim()) {
      setError("Title and abstract cannot be empty.");
      return;
    }

    // Validate before starting either API operation

    try {
      if (!revisionFile) {
        setError("Please select a revised PDF or Word file before resubmitting.");
        return;
      }

      validateManuscriptFile(revisionFile);
    } catch (err) {
      setError(err.message);
      return;
    }


    setResubmitting(true);
    setError("");
    setMessage("");

    let manuscriptFileUploaded = false;

    try {
      // Upload replacement manuscript file before changing manuscript status

        await uploadManuscriptFile(manuscriptId, revisionFile);
        manuscriptFileUploaded = true;


      // Resubmit updated title and abstract
      const response = await fetch(
        `${API_URL}/manuscripts/${manuscriptId}/resubmit`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            title: revisionTitle.trim(),
            abstract: revisionAbstract.trim(),
          }),
        }
      );

      await readResponse(response, "Manuscript resubmission failed");

      cancelRevision();

      const refreshed = await loadManuscripts();
      setMessage(
        refreshed
          ? `Manuscript #${manuscriptId} resubmitted successfully!`
          : `Manuscript #${manuscriptId} resubmitted, but the manuscript list could not be refreshed.`
      );
    } catch (err) {
      const prefix = manuscriptFileUploaded
        ? "The revised manuscript file was uploaded, but resubmission failed. "
        : "";

      setError(
        prefix + (err.message || "Manuscript resubmission failed.")
      );
    } finally {
      setResubmitting(false);
    }
  };

  // Dashboard statistics
  const draftCount = manuscripts.filter((manuscript) => manuscript.status === "draft").length;

  const acceptedCount = manuscripts.filter(
    (manuscript) => manuscript.status === "accepted"
  ).length;

  const submittedCount = manuscripts.filter(
    (manuscript) => manuscript.status === "submitted"
  ).length;

  const revisionCount = manuscripts.filter(
    (manuscript) => manuscript.status === "revision_required"
  ).length;

  const handleNotificationNavigate = (notification) => {
    const manuscriptId = notification.manuscript_id;

    const element = document.getElementById(
      `manuscript-${manuscriptId}`
    );

    if (element) {
      element.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  };

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div>
          <h1>Manuscript Submission System</h1>
          <p>Author Dashboard · {user.name}</p>
        </div>


        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <NotificationBell
            token={token}
            onNavigate={handleNotificationNavigate}
          />


          <button
            type="button"
            className="logout-button"
            onClick={onLogout}
          >
            Logout
          </button>
        </div>

      </header>

      <main className="dashboard-main">
        <FormattingPanel role="author" token={token} />
        <FinalEditingPanel ref={finalEditingPanel} role="author" token={token} onChanged={loadManuscripts} />
        <AuthorAgreementPanel token={token} onChanged={refreshAfterAgreement} />
        <InitialEditingPanel role="author" token={token} onChanged={loadManuscripts} />
        <h2>Welcome, {user.name}!</h2>

        <p className="dashboard-description">
          Manage your manuscripts and track submission status.
        </p>

        <div className="stats-grid">
          <div className="stat-card">
            <span>My Manuscripts</span>
            <strong>{manuscripts.length}</strong>
          </div>

          <div className="stat-card">
            <span>Drafts</span>
            <strong>{draftCount}</strong>
          </div>

          <div className="stat-card">
            <span>Accepted</span>
            <strong>{acceptedCount}</strong>
          </div>

          <div className="stat-card">
            <span>Submitted</span>
            <strong>{submittedCount}</strong>
          </div>

          <div className="stat-card">
            <span>Revision Required</span>
            <strong>{revisionCount}</strong>
          </div>
        </div>

        {error && (
          <div className="error-message" role="alert">
            {error}
          </div>
        )}

        {message && (
          <div className="success-message" role="status">
            {message}
          </div>
        )}

        <section className="manuscript-section">
          <div className="section-header">
            <h2>My Manuscripts</h2>

            <button
              type="button"
              className="new-submission-button"
              disabled={submitting}
              onClick={() => setShowForm(!showForm)}
            >
              {showForm ? "Cancel" : "+ New Draft"}
            </button>
          </div>

          {showForm && (
            <form
              className="submission-form"
              onSubmit={handleSubmit}
            >
              <h3>Create New Draft</h3>

              <label htmlFor="manuscript-title">
                Title
              </label>

              <input
                id="manuscript-title"
                type="text"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Enter manuscript title"
                maxLength={255}
                disabled={submitting}
                required
              />

              <label htmlFor="manuscript-abstract">
                Abstract
              </label>

              <textarea
                id="manuscript-abstract"
                value={abstract}
                onChange={(event) => setAbstract(event.target.value)}
                placeholder="Enter manuscript abstract"
                rows={5}
                disabled={submitting}
                required
              />

              <button type="submit" disabled={submitting}>
                {submitting
                  ? "Saving..."
                  : "Save Draft"}
              </button>
            </form>
          )}

          {loading ? (
            <p>Loading manuscripts...</p>
          ) : manuscripts.length === 0 ? (
            <p>No manuscripts or drafts yet.</p>
          ) : (
            <div className="manuscript-list">
              {manuscripts.map((manuscript) => {
                const manuscriptId = manuscript.manuscript_id;

                const needsRevision =
                  manuscript.status === "revision_required";
                const isDraft = manuscript.status === "draft";
                const isDraftEditing = draftEditingId === manuscriptId;

                const isEditing = editingId === manuscriptId;


                return (
                  <div
                    id={`manuscript-${manuscriptId}`}
                    className="manuscript-card"
                    key={manuscriptId}
                  >

                    <div className="manuscript-card-top">
                      <h3>{manuscript.title}</h3>

                      <span
                        className={`status-badge ${manuscript.status}`}
                      >
                        {typeof manuscript.status === "string" ? manuscript.status.replaceAll("_", " ") : "Unknown"}
                      </span>
                    </div>

                    <p className="manuscript-id">
                      Manuscript #{manuscriptId}
                    </p>

                    <p className="manuscript-abstract">
                      {manuscript.abstract}
                    </p>

                    <p className="manuscript-date">
                      Created:{" "}
                      {new Date(
                        manuscript.created_at
                      ).toLocaleDateString()}
                    </p>

                    <p className="manuscript-date">
                      Manuscript file: {manuscript.has_pdf ? "Available" : "Not uploaded"}
                    </p>

                    <div className="manuscript-actions">
                      <button
                        type="button"
                        className="download-button"
                        aria-expanded={progressId === manuscriptId}
                        onClick={() => setProgressId(progressId === manuscriptId ? null : manuscriptId)}
                      >
                        {progressId === manuscriptId ? "Close Progress" : "View Progress"}
                      </button>
                      {isDraft && (
                        <>
                          <input
                            type="file"
                            accept=".pdf,.docx,.doc"
                            className="hidden-file-input"
                            ref={(element) => {
                              fileInputs.current[manuscriptId] = element;
                            }}
                            onChange={(event) => {
                              handleUpload(
                                manuscriptId,
                                event.target.files?.[0]
                              );
                            }}
                          />

                          <button
                            type="button"
                            className="upload-button"
                            disabled={uploadingId !== null}
                            onClick={() => {
                              fileInputs.current[manuscriptId]?.click();
                            }}
                          >
                            {uploadingId === manuscriptId
                              ? "Uploading..."
                              : "Upload File"}
                          </button>
                        </>
                      )}

                      {manuscript.has_pdf && (
                        <button
                          type="button"
                          className="download-button"
                          disabled={downloadingId !== null}
                          onClick={() => handleDownload(manuscriptId)}
                        >
                          {downloadingId === manuscriptId
                            ? "Downloading..."
                            : "Download File"}
                        </button>
                      )}

                      {isDraft && (
                        <>
                          {!isDraftEditing && (
                            <button
                              type="button"
                              className="upload-button"
                              disabled={savingDraft || submittingDraftId !== null}
                              onClick={() => startDraftEdit(manuscript)}
                            >
                              Edit Draft
                            </button>
                          )}
                          <button
                            type="button"
                            className="upload-button"
                            disabled={!manuscript.has_pdf || submittingDraftId !== null || uploadingId !== null || savingDraft}
                            onClick={() => handleSubmitDraft(manuscript)}
                            title={!manuscript.has_pdf ? "Upload a PDF or Word file before submitting" : "Submit for review"}
                          >
                            {submittingDraftId === manuscriptId ? "Submitting..." : "Submit Manuscript"}
                          </button>
                        </>
                      )}

                      {needsRevision && !isEditing && (
                        <button
                          type="button"
                          className="upload-button"
                          disabled={resubmitting}
                          onClick={() => startRevision(manuscript)}
                        >
                          Revise Manuscript
                        </button>
                      )}
                    </div>

                    {progressId === manuscriptId && (
                      <ManuscriptProgress key={manuscriptId} manuscriptId={manuscriptId} token={token} />
                    )}

                    {isDraft && isDraftEditing && (
                      <form
                        className="submission-form"
                        onSubmit={(event) => handleSaveDraft(event, manuscriptId)}
                      >
                        <h3>Edit Draft #{manuscriptId}</h3>
                        <label htmlFor={`draft-title-${manuscriptId}`}>Title</label>
                        <input
                          id={`draft-title-${manuscriptId}`}
                          type="text"
                          value={draftTitle}
                          onChange={(event) => setDraftTitle(event.target.value)}
                          maxLength={255}
                          disabled={savingDraft}
                          required
                        />
                        <label htmlFor={`draft-abstract-${manuscriptId}`}>Abstract</label>
                        <textarea
                          id={`draft-abstract-${manuscriptId}`}
                          value={draftAbstract}
                          onChange={(event) => setDraftAbstract(event.target.value)}
                          rows={5}
                          disabled={savingDraft}
                          required
                        />
                        <div className="manuscript-actions">
                          <button type="submit" className="upload-button" disabled={savingDraft}>
                            {savingDraft ? "Saving..." : "Save Changes"}
                          </button>
                          <button type="button" className="download-button" onClick={cancelDraftEdit} disabled={savingDraft}>
                            Cancel
                          </button>
                        </div>
                      </form>
                    )}

                    {needsRevision && isEditing && (
                      <form
                        className="submission-form"
                        onSubmit={(event) =>
                          handleResubmit(event, manuscriptId)
                        }
                      >
                        <h3>
                          Revise Manuscript #{manuscriptId}
                        </h3>

                        <label
                          htmlFor={`revision-title-${manuscriptId}`}
                        >
                          Revised Title
                        </label>

                        <input
                          id={`revision-title-${manuscriptId}`}
                          type="text"
                          value={revisionTitle}
                          onChange={(event) =>
                            setRevisionTitle(event.target.value)
                          }
                          maxLength={255}
                          disabled={resubmitting}
                          required
                        />

                        <label
                          htmlFor={`revision-abstract-${manuscriptId}`}
                        >
                          Revised Abstract
                        </label>

                        <textarea
                          id={`revision-abstract-${manuscriptId}`}
                          value={revisionAbstract}
                          onChange={(event) =>
                            setRevisionAbstract(event.target.value)
                          }
                          rows={5}
                          disabled={resubmitting}
                          required
                        />

                        <label
                          htmlFor={`revision-file-${manuscriptId}`}
                        >
                          Revised manuscript file (PDF, DOCX, or DOC; required)
                        </label>

                        <input
                          id={`revision-file-${manuscriptId}`}
                          type="file"
                          accept=".pdf,.docx,.doc"
                          ref={revisionFileInput}
                          required
                          disabled={resubmitting}
                          onChange={(event) =>
                            setRevisionFile(
                              event.target.files?.[0] || null
                            )
                          }
                        />

                        <div className="manuscript-actions">
                          <button
                            type="submit"
                            className="upload-button"
                            disabled={resubmitting}
                          >
                            {resubmitting
                              ? "Resubmitting..."
                              : "Resubmit Manuscript"}
                          </button>

                          <button
                            type="button"
                            className="download-button"
                            onClick={cancelRevision}
                            disabled={resubmitting}
                          >
                            Cancel
                          </button>
                        </div>
                      </form>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default AuthorDashboard;
