
import { useEffect, useState } from "react";
import "./App.css";

import AuthorDashboard from "./AuthorDashboard";
import ReviewerDashboard from "./ReviewerDashboard";
import EditorDashboard from "./EditorDashboard";
import EICDashboard from "./EICDashboard";
import FormattingPanel from "./FormattingPanel";
import { PRODUCT_DISPLAY_NAME } from "./branding";
import RoleSelection from "./RoleSelection";
import { roleLabels } from "./roleLabels";

import { API_URL } from "./api";
import VerifyEmail from "./VerifyEmail";

function App() {
  const [isLogin, setIsLogin] = useState(true);
  const [selectedRole, setSelectedRole] = useState(null);

  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
  });

  const [user, setUser] = useState(null);

  const [token, setToken] = useState(
    () => sessionStorage.getItem("access_token")
  );

  const [loading, setLoading] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(Boolean(token));

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [needsEmailVerification, setNeedsEmailVerification] = useState(false);

  // Send requests to FastAPI
  const apiRequest = async (endpoint, options = {}) => {
    const response = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    });

    const contentType =
      response.headers.get("content-type") || "";

    let data;

    if (contentType.includes("application/json")) {
      data = await response.json();
    } else {
      const text = await response.text();
      data = {
        detail: text || "Request failed",
      };
    }

    if (!response.ok) {
      const detail = data.detail;
      const tunnelSessionError =
        response.status === 401 &&
        !contentType.includes("application/json") &&
        typeof detail === "string" &&
        detail.trim() === "Unauthorized";

      const requestError = new Error(
        tunnelSessionError
          ? "Your protected tunnel session may have expired. Refresh this page and sign in through Cloudflare again, then retry."
          : typeof detail === "string"
          ? detail
          : JSON.stringify(detail || "Request failed")
      );

      requestError.status = response.status;
      throw requestError;
    }

    return data;
  };

  // Get the current user's information
  const fetchCurrentUser = async (accessToken) => {
    return await apiRequest("/users/me", {
      method: "GET",
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });
  };

  // Restore login after refreshing the page
  useEffect(() => {
    if (!token) {
      setCheckingAuth(false);
      return;
    }

    let cancelled = false;

    setCheckingAuth(true);

    fetchCurrentUser(token)
      .then((currentUser) => {
        if (!cancelled) {
          setUser(currentUser);
          setError("");
        }
      })
      .catch((err) => {
        if (cancelled) return;

        setUser(null);

        if (err.status === 401 || err.status === 403) {
          sessionStorage.removeItem("access_token");
          setToken(null);
          setError("Your session has expired. Please sign in again.");
        } else {
          setError(
            "Unable to connect to the server. Please refresh the page and try again."
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setCheckingAuth(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  // Handle input changes
  const handleChange = (e) => {
    const { name, value } = e.target;

    setForm((previousForm) => ({
      ...previousForm,
      [name]: value,
    }));
  };

  // Handle login and registration
  const handleSubmit = async (e) => {
    e.preventDefault();

    setLoading(true);
    setError("");
    setMessage("");
    setNeedsEmailVerification(false);

    try {
      if (isLogin) {
        // Step 1: Login
        const loginData = await apiRequest("/users/login", {
          method: "POST",
          body: JSON.stringify({
            email: form.email,
            password: form.password,
          }),
        });

        // Step 2: Get user information
        const currentUser = await fetchCurrentUser(
          loginData.access_token
        );

        if (currentUser.role !== selectedRole) {
          setForm(previous => ({ ...previous, password: "" }));
          setError("The account does not match the selected sign-in role. Choose the correct role and try again.");
          return;
        }

        // Step 3: Save login state
        sessionStorage.setItem(
          "access_token",
          loginData.access_token
        );

        setUser(currentUser);
        setToken(loginData.access_token);

        setForm({
          name: "",
          email: "",
          password: "",
        });
      } else {
        // Register a new author account
        await apiRequest("/users/register", {
          method: "POST",
          body: JSON.stringify({
            name: form.name,
            email: form.email,
            password: form.password,
          }),
        });

        setMessage(
          "Registration successful! If a verification email was sent, please verify your email address before signing in."
        );

        setIsLogin(true);
        setSelectedRole("author");

        setForm({
          name: "",
          email: form.email,
          password: "",
        });
      }
    } catch (err) {
      const errorMessage = err.message || "Something went wrong.";
      setError(errorMessage);

      if (
        isLogin &&
        err.status === 403 &&
        errorMessage.includes("verify your email address")
      ) {
        setNeedsEmailVerification(true);
      }
    } finally {
      setLoading(false);
    }
  };

  // Resend email verification without requiring a login token.
  const handleResendVerification = async () => {
    if (!form.email || !form.password) {
      setError("Enter your email and password first.");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("");

    try {
      const result = await apiRequest("/users/resend-verification", {
        method: "POST",
        body: JSON.stringify({
          email: form.email,
          password: form.password,
        }),
      });

      if (result.sent) {
        setMessage("Verification email sent. Please check your inbox.");
      } else {
        setMessage(result.message || "No verification email was sent.");
      }
    } catch (err) {
      setError(err.message || "Unable to resend verification email.");
    } finally {
      setLoading(false);
    }
  };

  // Logout
  const handleLogout = () => {
    sessionStorage.removeItem("access_token");

    setToken(null);
    setUser(null);
    setCheckingAuth(false);

    setError("");
    setMessage("");

    setForm({
      name: "",
      email: "",
      password: "",
    });

    setIsLogin(true);
    setSelectedRole(null);
  };

  // Show loading screen while restoring login
  if (checkingAuth) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="brand-icon">M</div>

          <h1>Manuscript Submission System</h1>

          <p className="subtitle">
            Checking your session...
          </p>
        </div>
      </div>
    );
  }

  // Show Author Dashboard after login
  if (user && user.role === "author") {
    return (
      <AuthorDashboard
        user={user}
        token={token}
        onLogout={handleLogout}
      />
    );
  }

  if (user && user.role === "formatter") return <main><h1>Formatter Dashboard</h1><p>Welcome, {user.name}</p><button onClick={handleLogout}>Logout</button><FormattingPanel role="formatter" token={token} /></main>;

  // Show Reviewer Dashboard after login
  if (user && user.role === "reviewer") {
    return (
      <ReviewerDashboard
        user={user}
        token={token}
        onLogout={handleLogout}
      />
    );
  }

  // Show Editor Dashboard after login
  if (user && user.role === "editor") {
    return (
      <EditorDashboard
        user={user}
        token={token}
        onLogout={handleLogout}
      />
    );
  }

  if (user && user.role === "eic") {
    return <EICDashboard user={user} token={token} onLogout={handleLogout} />;
  }

  // Fallback for an unknown user role
  if (user) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="brand-icon">M</div>

          <h1>Welcome, {user.name}!</h1>

          <p className="subtitle">
            You have successfully signed in.
          </p>

          <div className="user-info">
            <p>
              <strong>User ID:</strong> {user.user_id}
            </p>

            <p>
              <strong>Name:</strong> {user.name}
            </p>

            <p>
              <strong>Email:</strong> {user.email}
            </p>

            <p>
              <strong>Role:</strong> {user.role}
            </p>
          </div>

          <button
            className="submit-button"
            onClick={handleLogout}
          >
            Logout
          </button>

          <p className="footer-text">
            No dashboard is available for this role.
          </p>
        </div>
      </div>
    );
  }

  // Login / Register page
  if (!selectedRole) {
    return <RoleSelection onSelect={(role) => { setSelectedRole(role); setError(""); setMessage(""); }} error={error} />;
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">{PRODUCT_DISPLAY_NAME}<span>.</span></div>

        <h1>{isLogin ? "Sign in" : "Create author account"}</h1>

        <p className="subtitle">
          {isLogin ? `Selected role: ${roleLabels[selectedRole]}` : "Registration creates an Author / Submitter account."}
        </p>
        <button type="button" className="change-role" onClick={() => { setSelectedRole(null); setIsLogin(true); setError(""); setMessage(""); setForm({ name: "", email: "", password: "" }); }}>← Choose a different role</button>

        <div className="auth-tabs">
          <button
            type="button"
            className={isLogin ? "active" : ""}
            onClick={() => {
              setIsLogin(true);
              setError("");
              setMessage("");
            }}
          >
            Login
          </button>

          <button
            type="button"
            className={!isLogin ? "active" : ""}
            onClick={() => {
              setIsLogin(false);
              setError("");
              setMessage("");
            }}
          >
            Register
          </button>
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

        {isLogin && needsEmailVerification && (
            <button
              type="button"
              className="change-role"
              onClick={handleResendVerification}
              disabled={loading}
            >
              {loading ? "Sending..." : "Resend Verification Email"}
            </button>
          )}

        <form onSubmit={handleSubmit}>
          {!isLogin && (
            <div className="form-group">
              <label htmlFor="name">
                Full Name
              </label>

              <input
                id="name"
                type="text"
                name="name"
                placeholder="Enter your full name"
                value={form.name}
                onChange={handleChange}
                required
              />
            </div>
          )}

          <div className="form-group">
            <label htmlFor="email">
              Email Address
            </label>

            <input
              id="email"
              type="email"
              name="email"
              placeholder="Enter your email"
              value={form.email}
              onChange={handleChange}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">
              Password
            </label>

            <input
              id="password"
              type="password"
              name="password"
              placeholder="Enter your password"
              value={form.password}
              onChange={handleChange}
              minLength={isLogin ? 1 : 8}
              required
            />
          </div>

          <button
            className="submit-button"
            type="submit"
            disabled={loading}
          >
            {loading
              ? "Please wait..."
              : isLogin
                ? "Sign In"
                : "Create Account"}
          </button>
        </form>

        <p className="footer-text">
          Manuscript Submission & Peer Review Platform
        </p>
      </div>
    </div>
  );
}

function AppRouter() {
  if (window.location.pathname === "/verify-email") {
    return <VerifyEmail />;
  }

  return <App />;
}

export default AppRouter;
