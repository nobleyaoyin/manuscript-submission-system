
import { useCallback, useEffect, useState } from "react";
import "./NotificationBell.css";

import { API_URL } from "./api";

function NotificationBell({ token, onNavigate }) {
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const apiRequest = useCallback(
    async (endpoint, options = {}) => {
      const response = await fetch(`${API_URL}${endpoint}`, {
        ...options,
        headers: {
          Authorization: `Bearer ${token}`,
          ...options.headers,
        },
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Request failed");
      }

      return data;
    },
    [token]
  );

  const loadNotifications = useCallback(async () => {
    if (!token) return;

    try {
      const [items, count] = await Promise.all([
        apiRequest("/notifications/my"),
        apiRequest("/notifications/unread-count"),
      ]);

      setNotifications(items);
      setUnreadCount(count.unread_count);
      setError("");
    } catch (err) {
      setError(err.message || "Failed to load notifications");
    }
  }, [apiRequest, token]);


  useEffect(() => {
    // Load notifications immediately
    loadNotifications();

    // Automatically refresh every 30 seconds
    const intervalId = setInterval(() => {
      loadNotifications();
    }, 30000);

    // Stop polling when the component unmounts
    return () => {
      clearInterval(intervalId);
    };
  }, [loadNotifications]);


  const handleToggle = async () => {
    if (!isOpen) {
      setLoading(true);
      await loadNotifications();
      setLoading(false);
    }

    setIsOpen(!isOpen);
  };

  const handleMarkAsRead = async (notificationId) => {
    try {
      await apiRequest(
        `/notifications/${notificationId}/read`,
        { method: "PATCH" }
      );

      await loadNotifications();
    } catch (err) {
      setError(err.message || "Failed to update notification");
    }
  };

  const handleNavigate = (notification) => {
    if (!notification.manuscript_id || !onNavigate) {
      return;
    }

    setIsOpen(false);
    onNavigate(notification);
  };

  return (
    <div className="notification-container">
      <button
        type="button"
        className="notification-bell"
        onClick={handleToggle}
        aria-label="Notifications"
        aria-expanded={isOpen}
      >
        <span aria-hidden="true">🔔</span>

        {unreadCount > 0 && (
          <span className="notification-badge">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="notification-panel">
          <div className="notification-header">
            <strong>Notifications</strong>

            <button
              type="button"
              onClick={() => setIsOpen(false)}
              aria-label="Close notifications"
            >
              ×
            </button>
          </div>

          {loading && <p>Loading...</p>}

          {error && (
            <p className="notification-error">{error}</p>
          )}

          {!loading && notifications.length === 0 && (
            <p className="notification-empty">
              No notifications yet.
            </p>
          )}

          {!loading &&
            notifications.map((notification) => (
              <div
                key={notification.notification_id}
                className={
                  notification.is_read
                    ? "notification-item"
                    : "notification-item unread"
                }
              >

                <button
                  type="button"
                  className="notification-link"
                  onClick={() => handleNavigate(notification)}
                  disabled={!notification.manuscript_id || !onNavigate}
                >
                  {notification.message}
                </button>

                <small>
                  {new Date(
                    notification.created_at.endsWith("Z")
                      ? notification.created_at
                      : `${notification.created_at}Z`
                  ).toLocaleString()}
                </small>

                {!notification.is_read && (
                  <button
                    type="button"
                    onClick={() =>
                      handleMarkAsRead(
                        notification.notification_id
                      )
                    }
                  >
                    Mark as Read
                  </button>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export default NotificationBell;
