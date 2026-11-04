
import { useCallback, useEffect, useRef, useState } from "react";
import "./EditorDashboard.css";

const API_URL = "http://127.0.0.1:8000";

function EditorDashboard({ user, token, onLogout }) {
  const [manuscripts, setManuscripts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [expandedId, setExpandedId] = useState(null);
  const [reviewerIds, setReviewerIds] = useState({});
  const [decisions, setDecisions] = useState({});
  const [reviewHistory, setReviewHistory] = useState({});
  const [historyLoadingId, setHistoryLoadingId] = useState(null);
  const [actionLoadingId, setActionLoadingId] = useState(null);

  const actionLock = useRef(false);

  // Read API response and handle errors
  const readResponse = async (response, fallback) => {
    const contentType =
      response.headers.get("content-type") || "";

    let data;

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
            ? detail
                .map((item) => item.msg || JSON.stringify(item))
                .join("; ")
            : fallback
      );
    }

    return data;
  };

  // Send authenticated API requests
  const apiRequest = useCallback(
    async (endpoint, options = {}) => {
      const response = await fetch(`${API_URL}${endpoint}`, {
        ...options,
        headers: {
          Authorization: `Bearer ${token}`,
          ...options.headers,
        },
      });

      return await readResponse(response, "Request failed");
    },
    [token]
  );

  // Load editor dashboard and summary
  const loadDashboard = useCallback(async () => {
    setLoading(true);

    try {
      const [manuscriptsData, summaryData] = await Promise.all([
        apiRequest("/manuscripts/dashboard"),
        apiRequest("/manuscripts/dashboard/summary"),
      ]);

      if (!Array.isArray(manuscriptsData)) {
        throw new Error("Invalid manuscript data received.");
      }

      setManuscripts(manuscriptsData);
      setSummary(summaryData);

      return true;
    } catch (err) {
      setError(err.message || "Failed to load dashboard.");
      return false;
    } finally {
      setLoading(false);
    }
  }, [apiRequest]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  // Format workflow status
  const formatStatus = (value) =>
    (value || "").replaceAll("_", " ");

  // Determine active review round
  const getActiveRound = (manuscript) =>
    manuscript.status === "revised"
      ? 2
      : manuscript.review_round || 1;

  // Avoid using previous-round counts for revised manuscripts
  const getCurrentRoundProgress = (manuscript) => {
    const activeRound = getActiveRound(manuscript);

    if (activeRound !== manuscript.review_round) {
      return {
        total: 0,
        completed: 0,
        pending: 0,
        reviewStatus: "not_assigned",
      };
    }

    return {
      total: manuscript.total_reviewers ?? 0,
      completed: manuscript.completed_reviews ?? 0,
      pending: manuscript.pending_reviews ?? 0,
      reviewStatus: manuscript.review_status || "not_assigned",
    };
  };

  // Expand or collapse manuscript actions
  const toggleActions = (manuscriptId) => {
    setExpandedId((previous) =>
      previous === manuscriptId ? null : manuscriptId
    );

    setError("");
    setMessage("");
  };

  // Manually refresh dashboard
  const handleRefresh = async () => {
    if (actionLock.current || loading) return;

    setError("");
    setMessage("");

    await loadDashboard();
  };

  // Download manuscript PDF
  const handleDownload = async (manuscriptId) => {
    if (actionLock.current) return;

    actionLock.current = true;
    setActionLoadingId(manuscriptId);
    setError("");
    setMessage("");

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
        await readResponse(response, "Failed to download PDF");
        return;
      }

      const blob = await response.blob();

      if (blob.size === 0) {
        throw new Error("Downloaded PDF is empty.");
      }

      const url = URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = url;
      link.download = `manuscript_${manuscriptId}.pdf`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err.message || "Failed to download PDF.");
    } finally {
      actionLock.current = false;
      setActionLoadingId(null);
    }
  };

  // Assign reviewer
  const handleAssignReviewer = async (manuscript) => {
    if (actionLock.current) return;

    const manuscriptId = manuscript.manuscript_id;
    const reviewerId = Number(reviewerIds[manuscriptId]);
    const progress = getCurrentRoundProgress(manuscript);

    if (!Number.isInteger(reviewerId) || reviewerId <= 0) {
      setError("Please enter a valid Reviewer ID.");
      return;
    }

    if (
      !["submitted", "revised"].includes(manuscript.status) ||
      (progress.total > 0 && progress.pending === 0)
    ) {
      setError(
        "Reviewer assignment is unavailable for this manuscript."
      );
      return;
    }

    actionLock.current = true;
    setActionLoadingId(manuscriptId);
    setError("");
    setMessage("");

    try {
      await apiRequest("/assignments/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          manuscript_id: manuscriptId,
          reviewer_id: reviewerId,
          review_round: getActiveRound(manuscript),
        }),
      });

      setReviewerIds((previous) => ({
        ...previous,
        [manuscriptId]: "",
      }));

      const refreshed = await loadDashboard();

      if (refreshed) {
        setMessage(
          `Reviewer #${reviewerId} assigned to Manuscript #${manuscriptId}.`
        );
      } else {
        setMessage(
          `Reviewer #${reviewerId} assigned successfully, but the dashboard could not be refreshed.`
        );
      }
    } catch (err) {
      setError(err.message || "Reviewer assignment failed.");
    } finally {
      actionLock.current = false;
      setActionLoadingId(null);
    }
  };

  // Load review history
  const handleViewReviews = async (manuscriptId) => {
    if (historyLoadingId !== null) return;

    setHistoryLoadingId(manuscriptId);
    setError("");

    try {
      const data = await apiRequest(
        `/reviews/manuscript/${manuscriptId}`
      );

      if (!Array.isArray(data)) {
        throw new Error("Invalid review history received.");
      }

      setReviewHistory((previous) => ({
        ...previous,
        [manuscriptId]: data,
      }));
    } catch (err) {
      setError(err.message || "Failed to load review history.");
    } finally {
      setHistoryLoadingId(null);
    }
  };

  // Submit editor decision
  const handleDecision = async (manuscript) => {
    if (actionLock.current) return;

    const manuscriptId = manuscript.manuscript_id;
    const decision = decisions[manuscriptId];
    const progress = getCurrentRoundProgress(manuscript);

    const canDecide =
      ["submitted", "revised"].includes(manuscript.status) &&
      progress.total > 0 &&
      progress.pending === 0 &&
      progress.completed === progress.total;

    if (!canDecide) {
      setError(
        "The current review round must be completed before making a decision."
      );
      return;
    }

    if (
      !["accepted", "rejected", "revision_required"].includes(
        decision
      )
    ) {
      setError("Please select a valid editor decision.");
      return;
    }

    const confirmed = window.confirm(
      `Make decision "${formatStatus(decision)}" for Manuscript #${manuscriptId}?`
    );

    if (!confirmed) return;

    actionLock.current = true;
    setActionLoadingId(manuscriptId);
    setError("");
    setMessage("");

    try {
      await apiRequest(
        `/manuscripts/${manuscriptId}/decision`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ decision }),
        }
      );

      setDecisions((previous) => ({
        ...previous,
        [manuscriptId]: "",
      }));

      const refreshed = await loadDashboard();

      if (refreshed) {
        setMessage(
          `Manuscript #${manuscriptId} updated to "${formatStatus(decision)}".`
        );
      } else {
        setMessage(
          `Decision saved for Manuscript #${manuscriptId}, but the dashboard could not be refreshed.`
        );
      }
    } catch (err) {
      setError(err.message || "Editor decision failed.");
    } finally {
      actionLock.current = false;
      setActionLoadingId(null);
    }
  };

  return (
    <div className="editor-page">
      <header className="editor-header">
        <div>
          <h1>Manuscript Submission System</h1>
          <p>Editor Dashboard · {user.name}</p>
        </div>

        <button
          type="button"
          className="editor-logout"
          onClick={onLogout}
        >
          Logout
        </button>
      </header>

      <main className="editor-main">
        <div className="editor-heading">
          <div>
            <h2>Welcome, {user.name}!</h2>
            <p>Monitor submissions and manage peer reviews.</p>
          </div>

          <button
            type="button"
            className="editor-refresh"
            onClick={handleRefresh}
            disabled={loading || actionLoadingId !== null}
          >
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>

        {error && (
          <div className="editor-error" role="alert">
            {error}
          </div>
        )}

        {message && (
          <div className="editor-success" role="status">
            {message}
          </div>
        )}

        {loading ? (
          <p>Loading dashboard...</p>
        ) : (
          <>
            {/* Dashboard statistics */}
            <div className="editor-stats">
              {[
                ["Total Manuscripts", summary?.total_manuscripts],
                ["Not Assigned", summary?.not_assigned],
                ["In Progress", summary?.in_progress],
                ["Ready for Decision", summary?.ready_for_decision],
                ["Revision Required", summary?.revision_required],
                ["Decision Finalized", summary?.decision_finalized],
              ].map(([label, value]) => (
                <div className="editor-stat-card" key={label}>
                  <span>{label}</span>
                  <strong>{value ?? 0}</strong>
                </div>
              ))}
            </div>

            {/* Manuscript list */}
            <section className="editor-section">
              <h2>All Manuscripts</h2>

              {manuscripts.length === 0 ? (
                <p>No manuscripts found.</p>
              ) : (
                <div className="editor-list">
                  {manuscripts.map((manuscript) => {
                    const manuscriptId = manuscript.manuscript_id;
                    const isExpanded = expandedId === manuscriptId;
                    const isBusy = actionLoadingId !== null;

                    const activeRound = getActiveRound(manuscript);
                    const progress =
                      getCurrentRoundProgress(manuscript);

                    const isFinalized = [
                      "accepted",
                      "rejected",
                    ].includes(manuscript.status);

                    const isOpen = [
                      "submitted",
                      "revised",
                    ].includes(manuscript.status);

                    // Stop assigning after all current reviews finish
                    const canAssign =
                      isOpen &&
                      (progress.total === 0 || progress.pending > 0);

                    const canDecide =
                      isOpen &&
                      progress.total > 0 &&
                      progress.pending === 0 &&
                      progress.completed === progress.total;

                    return (
                      <div
                        className="editor-card"
                        key={manuscriptId}
                      >
                        <div className="editor-card-top">
                          <div>
                            <h3>{manuscript.title}</h3>
                            <p>
                              Manuscript #{manuscriptId}
                              {" · "}
                              Review Round {activeRound}
                            </p>
                          </div>

                          <span
                            className={`editor-status ${manuscript.status}`}
                          >
                            {formatStatus(manuscript.status)}
                          </span>
                        </div>

                        <div className="editor-progress">
                          <span>
                            Completed Reviews:{" "}
                            <strong>
                              {progress.completed}/{progress.total}
                            </strong>
                          </span>

                          <span>
                            Pending:{" "}
                            <strong>{progress.pending}</strong>
                          </span>
                        </div>

                        <div className="editor-review-status">
                          Review Status:{" "}
                          <strong>
                            {formatStatus(progress.reviewStatus)}
                          </strong>
                        </div>

                        <button
                          type="button"
                          className="editor-manage-button"
                          onClick={() => toggleActions(manuscriptId)}
                        >
                          {isExpanded
                            ? "Hide Actions"
                            : "Manage Manuscript"}
                        </button>

                        {isExpanded && (
                          <div className="editor-actions">
                            {/* PDF Download */}
                            <div className="editor-action-block">
                              <h4>Manuscript PDF</h4>

                              <button
                                type="button"
                                className="editor-secondary-button"
                                disabled={isBusy}
                                onClick={() =>
                                  handleDownload(manuscriptId)
                                }
                              >
                                {actionLoadingId === manuscriptId
                                  ? "Processing..."
                                  : "Download PDF"}
                              </button>
                            </div>

                            {/* Assign Reviewer */}
                            {canAssign && (
                              <div className="editor-action-block">
                                <h4>Assign Reviewer</h4>

                                <p>
                                  Assign a reviewer for Review Round{" "}
                                  {activeRound}.
                                </p>

                                <div className="editor-action-row">
                                  <input
                                    type="number"
                                    min="1"
                                    step="1"
                                    placeholder="Reviewer ID (e.g. 3)"
                                    value={
                                      reviewerIds[manuscriptId] ?? ""
                                    }
                                    disabled={isBusy}
                                    onChange={(event) =>
                                      setReviewerIds((previous) => ({
                                        ...previous,
                                        [manuscriptId]:
                                          event.target.value,
                                      }))
                                    }
                                  />

                                  <button
                                    type="button"
                                    className="editor-primary-button"
                                    disabled={isBusy}
                                    onClick={() =>
                                      handleAssignReviewer(manuscript)
                                    }
                                  >
                                    {actionLoadingId === manuscriptId
                                      ? "Processing..."
                                      : "Assign Reviewer"}
                                  </button>
                                </div>
                              </div>
                            )}

                            {/* Review History */}
                            <div className="editor-action-block">
                              <h4>Review History</h4>

                              <button
                                type="button"
                                className="editor-secondary-button"
                                disabled={
                                  historyLoadingId !== null
                                }
                                onClick={() =>
                                  handleViewReviews(manuscriptId)
                                }
                              >
                                {historyLoadingId === manuscriptId
                                  ? "Loading..."
                                  : "View Reviews"}
                              </button>

                              {reviewHistory[manuscriptId] && (
                                <div className="editor-review-list">
                                  {reviewHistory[manuscriptId].length === 0 ? (
                                    <p>No reviews submitted yet.</p>
                                  ) : (
                                    reviewHistory[manuscriptId].map(
                                      (review) => (
                                        <div
                                          className="editor-review-item"
                                          key={review.review_id}
                                        >
                                          <p>
                                            <strong>
                                              Review Round{" "}
                                              {review.review_round}
                                            </strong>
                                            {" · "}
                                            Reviewer #{review.reviewer_id}
                                          </p>

                                          <p>
                                            Recommendation:{" "}
                                            <strong>
                                              {formatStatus(
                                                review.recommendation
                                              )}
                                            </strong>
                                          </p>

                                          <p>{review.comments}</p>
                                        </div>
                                      )
                                    )
                                  )}
                                </div>
                              )}
                            </div>

                            {/* Editor Decision */}
                            {!isFinalized && (
                              <div className="editor-action-block">
                                <h4>Editor Decision</h4>

                                {canDecide ? (
                                  <>
                                    <p>
                                      All assigned reviewers have completed
                                      their reviews.
                                    </p>

                                    <div className="editor-action-row">
                                      <select
                                        value={
                                          decisions[manuscriptId] || ""
                                        }
                                        disabled={isBusy}
                                        onChange={(event) =>
                                          setDecisions((previous) => ({
                                            ...previous,
                                            [manuscriptId]:
                                              event.target.value,
                                          }))
                                        }
                                      >
                                        <option value="">
                                          Select decision
                                        </option>
                                        <option value="accepted">
                                          Accept
                                        </option>
                                        <option value="rejected">
                                          Reject
                                        </option>
                                        <option value="revision_required">
                                          Request Revision
                                        </option>
                                      </select>

                                      <button
                                        type="button"
                                        className="editor-primary-button"
                                        disabled={
                                          isBusy ||
                                          !decisions[manuscriptId]
                                        }
                                        onClick={() =>
                                          handleDecision(manuscript)
                                        }
                                      >
                                        {actionLoadingId === manuscriptId
                                          ? "Processing..."
                                          : "Submit Decision"}
                                      </button>
                                    </div>
                                  </>
                                ) : (
                                  <p>
                                    A decision is unavailable until the
                                    current review round is complete.
                                  </p>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

export default EditorDashboard;
