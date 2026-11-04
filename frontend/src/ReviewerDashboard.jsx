
import { useCallback, useEffect, useRef, useState } from "react";
import "./ReviewerDashboard.css";

const API_URL = "http://127.0.0.1:8000";

function ReviewerDashboard({ user, token, onLogout }) {
  const [assignments, setAssignments] = useState([]);
  const [reviews, setReviews] = useState({});

  const [loading, setLoading] = useState(true);
  const [submittingId, setSubmittingId] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // Prevent repeated clicks before React updates the UI
  const submittingRef = useRef(false);

  // Handle API responses
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

  // Load assignments for the current reviewer
  const loadAssignments = useCallback(async () => {
    setLoading(true);

    try {
      const response = await fetch(
        `${API_URL}/assignments/my`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const contentType =
        response.headers.get("content-type") || "";

      const data = contentType.includes("application/json")
        ? await response.json()
        : null;

      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : "Failed to load assignments"
        );
      }

      if (!Array.isArray(data)) {
        throw new Error("Invalid assignment data received.");
      }

      setAssignments(data);
      return true;
    } catch (err) {
      setError(err.message || "Failed to load assignments.");
      return false;
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadAssignments();
  }, [loadAssignments]);

  // Manually refresh assignments
  const handleRefresh = async () => {
    setError("");
    setMessage("");
    await loadAssignments();
  };

  // Update review form values
  const handleReviewChange = (assignmentId, field, value) => {
    setReviews((previous) => ({
      ...previous,
      [assignmentId]: {
        ...previous[assignmentId],
        [field]: value,
      },
    }));
  };

  // Submit a review
  const handleSubmitReview = async (assignmentId) => {
    if (submittingRef.current) return;

    const review = reviews[assignmentId] || {};

    if (!review.comments?.trim() || !review.recommendation) {
      setError("Please enter comments and select a recommendation.");
      return;
    }

    if (
      !["accept", "revision", "reject"].includes(
        review.recommendation
      )
    ) {
      setError("Please select a valid recommendation.");
      return;
    }

    submittingRef.current = true;
    setSubmittingId(assignmentId);

    setError("");
    setMessage("");

    try {
      const response = await fetch(`${API_URL}/reviews/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          assignment_id: assignmentId,
          comments: review.comments.trim(),
          recommendation: review.recommendation,
        }),
      });

      await readResponse(response, "Review submission failed");

      // Clear the submitted review form
      setReviews((previous) => {
        const updated = { ...previous };
        delete updated[assignmentId];
        return updated;
      });

      // Reload assignments to show the completed status
      const refreshed = await loadAssignments();

      if (refreshed) {
        setMessage(
          `Review submitted successfully for Assignment #${assignmentId}.`
        );
      } else {
        setMessage(
          `Review submitted for Assignment #${assignmentId}, but the assignment list could not be refreshed.`
        );
      }
    } catch (err) {
      setError(err.message || "Review submission failed.");
    } finally {
      submittingRef.current = false;
      setSubmittingId(null);
    }
  };

  // Download assigned manuscript PDF
  const handleDownload = async (manuscriptId) => {
    if (downloadingId !== null) return;

    setDownloadingId(manuscriptId);
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
        await readResponse(response, "PDF download failed");
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

      setTimeout(() => {
        URL.revokeObjectURL(url);
      }, 1000);
    } catch (err) {
      setError(err.message || "PDF download failed.");
    } finally {
      setDownloadingId(null);
    }
  };

  // Dashboard statistics
  const completedCount = assignments.filter(
    (assignment) => assignment.status === "completed"
  ).length;

  const pendingCount = assignments.filter(
    (assignment) => assignment.status === "assigned"
  ).length;

  return (
    <div className="reviewer-page">
      <header className="reviewer-header">
        <div>
          <h1>Manuscript Submission System</h1>
          <p>Reviewer Dashboard · {user.name}</p>
        </div>

        <button
          type="button"
          onClick={onLogout}
          className="reviewer-logout"
        >
          Logout
        </button>
      </header>

      <main className="reviewer-main">
        <h2>Welcome, {user.name}!</h2>

        <p className="reviewer-description">
          Review assigned manuscripts and submit your recommendations.
        </p>

        <div className="reviewer-stats">
          <div className="reviewer-stat-card">
            <span>Total Assignments</span>
            <strong>{assignments.length}</strong>
          </div>

          <div className="reviewer-stat-card">
            <span>Completed</span>
            <strong>{completedCount}</strong>
          </div>

          <div className="reviewer-stat-card">
            <span>Pending</span>
            <strong>{pendingCount}</strong>
          </div>
        </div>

        {error && (
          <div className="reviewer-error" role="alert">
            {error}
          </div>
        )}

        {message && (
          <div className="reviewer-success" role="status">
            {message}
          </div>
        )}

        <section className="reviewer-section">
          <div className="reviewer-section-header">
            <h2>My Review Assignments</h2>

            <button
              type="button"
              className="reviewer-download"
              onClick={handleRefresh}
              disabled={loading || submittingId !== null}
            >
              {loading ? "Loading..." : "Refresh"}
            </button>
          </div>

          {loading ? (
            <p>Loading assignments...</p>
          ) : assignments.length === 0 ? (
            <p>No review assignments found.</p>
          ) : (
            <div className="reviewer-list">
              {assignments.map((assignment) => {
                const assignmentId = assignment.assignment_id;
                const review = reviews[assignmentId] || {};

                const isCompleted =
                  assignment.status === "completed";

                const isAssigned =
                  assignment.status === "assigned";

                const isSubmitting =
                  submittingId === assignmentId;

                return (
                  <div
                    className="reviewer-card"
                    key={assignmentId}
                  >
                    <div className="reviewer-card-top">
                      <h3>
                        Manuscript #{assignment.manuscript_id}
                      </h3>

                      <span
                        className={`reviewer-status ${assignment.status}`}
                      >
                        {assignment.status}
                      </span>
                    </div>

                    <p className="reviewer-meta">
                      Assignment #{assignmentId}
                      {" · "}
                      Review Round {assignment.review_round}
                    </p>

                    <p className="reviewer-meta">
                      Assigned:{" "}
                      {new Date(
                        assignment.assigned_at
                      ).toLocaleDateString()}
                    </p>

                    <button
                      type="button"
                      className="reviewer-download"
                      disabled={downloadingId !== null}
                      onClick={() =>
                        handleDownload(assignment.manuscript_id)
                      }
                    >
                      {downloadingId === assignment.manuscript_id
                        ? "Downloading..."
                        : "Download Manuscript PDF"}
                    </button>

                    {isAssigned && (
                      <div className="review-form">
                        <h4>Submit Review</h4>

                        <label htmlFor={`comments-${assignmentId}`}>
                          Review Comments
                        </label>

                        <textarea
                          id={`comments-${assignmentId}`}
                          rows={4}
                          placeholder="Enter your review comments..."
                          value={review.comments || ""}
                          disabled={submittingId !== null}
                          onChange={(event) =>
                            handleReviewChange(
                              assignmentId,
                              "comments",
                              event.target.value
                            )
                          }
                        />

                        <label
                          htmlFor={`recommendation-${assignmentId}`}
                        >
                          Recommendation
                        </label>

                        <select
                          id={`recommendation-${assignmentId}`}
                          value={review.recommendation || ""}
                          disabled={submittingId !== null}
                          onChange={(event) =>
                            handleReviewChange(
                              assignmentId,
                              "recommendation",
                              event.target.value
                            )
                          }
                        >
                          <option value="">
                            Select recommendation
                          </option>

                          <option value="accept">
                            Accept
                          </option>

                          <option value="revision">
                            Revision
                          </option>

                          <option value="reject">
                            Reject
                          </option>
                        </select>

                        <button
                          type="button"
                          className="reviewer-submit"
                          disabled={submittingId !== null}
                          onClick={() =>
                            handleSubmitReview(assignmentId)
                          }
                        >
                          {isSubmitting
                            ? "Submitting..."
                            : "Submit Review"}
                        </button>
                      </div>
                    )}

                    {isCompleted && (
                      <p className="reviewer-completed-message">
                        Review already submitted.
                      </p>
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

export default ReviewerDashboard;
