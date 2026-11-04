
import { useCallback, useEffect, useRef, useState } from "react";
import "./AuthorDashboard.css";

const API_URL = "http://127.0.0.1:8000";
const MAX_FILE_SIZE = 10 * 1024 * 1024;

function AuthorDashboard({ user, token, onLogout }) {
  const [manuscripts, setManuscripts] = useState([]);

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

  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

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
    } catch (err) {
      setError(err.message || "Failed to load manuscripts.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadManuscripts();
  }, [loadManuscripts]);

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

      await readResponse(response, "Manuscript submission failed");

      setTitle("");
      setAbstract("");
      setShowForm(false);

      await loadManuscripts();

      setMessage("Manuscript submitted successfully!");
    } catch (err) {
      setError(err.message || "Manuscript submission failed.");
    } finally {
      setSubmitting(false);
    }
  };

  // Shared PDF validation
  const validatePDF = (file) => {
    if (!file) {
      throw new Error("Please select a PDF file.");
    }

    if (
      file.type !== "application/pdf" ||
      !file.name.toLowerCase().endsWith(".pdf")
    ) {
      throw new Error("Please select a valid PDF file.");
    }

    if (file.size === 0) {
      throw new Error("The PDF file is empty.");
    }

    if (file.size > MAX_FILE_SIZE) {
      throw new Error("PDF file exceeds the 10 MB limit.");
    }
  };

  // Shared PDF upload request
  const uploadPDF = async (manuscriptId, file) => {
    validatePDF(file);

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

    return await readResponse(response, "PDF upload failed");
  };

  // Upload PDF for submitted manuscript
  const handleUpload = async (manuscriptId, file) => {
    if (!file || uploadingId !== null) return;

    setError("");
    setMessage("");
    setUploadingId(manuscriptId);

    try {
      await uploadPDF(manuscriptId, file);

      setMessage(
        `PDF uploaded successfully for Manuscript #${manuscriptId}.`
      );
    } catch (err) {
      setError(err.message || "PDF upload failed.");
    } finally {
      setUploadingId(null);

      if (fileInputs.current[manuscriptId]) {
        fileInputs.current[manuscriptId].value = "";
      }
    }
  };

  // Download PDF
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
        await readResponse(response, "PDF download failed");
        return;
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = url;
      link.download = `manuscript_${manuscriptId}.pdf`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err.message || "PDF download failed.");
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
      if (revisionFile) {
        validatePDF(revisionFile);
      }
    } catch (err) {
      setError(err.message);
      return;
    }

    setResubmitting(true);
    setError("");
    setMessage("");

    let pdfUploaded = false;

    try {
      // Upload replacement PDF before changing manuscript status
      if (revisionFile) {
        await uploadPDF(manuscriptId, revisionFile);
        pdfUploaded = true;
      }

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

      await loadManuscripts();

      setMessage(
        `Manuscript #${manuscriptId} resubmitted successfully!`
      );
    } catch (err) {
      const prefix = pdfUploaded
        ? "The revised PDF was uploaded, but resubmission failed. "
        : "";

      setError(
        prefix + (err.message || "Manuscript resubmission failed.")
      );
    } finally {
      setResubmitting(false);
    }
  };

  // Dashboard statistics
  const acceptedCount = manuscripts.filter(
    (manuscript) => manuscript.status === "accepted"
  ).length;

  const submittedCount = manuscripts.filter(
    (manuscript) => manuscript.status === "submitted"
  ).length;

  const revisionCount = manuscripts.filter(
    (manuscript) => manuscript.status === "revision_required"
  ).length;

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div>
          <h1>Manuscript Submission System</h1>
          <p>Author Dashboard · {user.name}</p>
        </div>

        <button
          type="button"
          className="logout-button"
          onClick={onLogout}
        >
          Logout
        </button>
      </header>

      <main className="dashboard-main">
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
              {showForm ? "Cancel" : "+ New Submission"}
            </button>
          </div>

          {showForm && (
            <form
              className="submission-form"
              onSubmit={handleSubmit}
            >
              <h3>Submit New Manuscript</h3>

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
                  ? "Submitting..."
                  : "Submit Manuscript"}
              </button>
            </form>
          )}

          {loading ? (
            <p>Loading manuscripts...</p>
          ) : manuscripts.length === 0 ? (
            <p>No manuscripts submitted yet.</p>
          ) : (
            <div className="manuscript-list">
              {manuscripts.map((manuscript) => {
                const manuscriptId = manuscript.manuscript_id;

                const needsRevision =
                  manuscript.status === "revision_required";

                const isEditing = editingId === manuscriptId;

                return (
                  <div
                    className="manuscript-card"
                    key={manuscriptId}
                  >
                    <div className="manuscript-card-top">
                      <h3>{manuscript.title}</h3>

                      <span
                        className={`status-badge ${manuscript.status}`}
                      >
                        {manuscript.status.replaceAll("_", " ")}
                      </span>
                    </div>

                    <p className="manuscript-id">
                      Manuscript #{manuscriptId}
                    </p>

                    <p className="manuscript-abstract">
                      {manuscript.abstract}
                    </p>

                    <p className="manuscript-date">
                      Submitted:{" "}
                      {new Date(
                        manuscript.created_at
                      ).toLocaleDateString()}
                    </p>

                    <div className="manuscript-actions">
                      {manuscript.status === "submitted" && (
                        <>
                          <input
                            type="file"
                            accept=".pdf,application/pdf"
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
                              : "Upload PDF"}
                          </button>
                        </>
                      )}

                      <button
                        type="button"
                        className="download-button"
                        disabled={downloadingId !== null}
                        onClick={() => handleDownload(manuscriptId)}
                      >
                        {downloadingId === manuscriptId
                          ? "Downloading..."
                          : "Download PDF"}
                      </button>

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
                          htmlFor={`revision-pdf-${manuscriptId}`}
                        >
                          Revised PDF (optional)
                        </label>

                        <input
                          id={`revision-pdf-${manuscriptId}`}
                          type="file"
                          accept=".pdf,application/pdf"
                          ref={revisionFileInput}
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
