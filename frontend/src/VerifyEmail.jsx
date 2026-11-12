import { useEffect, useState } from "react";
import { verifyEmailOnce } from "./emailVerificationRequest";

export default function VerifyEmail() {
  const [status, setStatus] = useState("verifying");
  const [message, setMessage] = useState(
    "Verifying your email address..."
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");

    if (!token) {
      setStatus("error");
      setMessage("Verification token is missing.");
      return;
    }

    // Prevent accidental token exposure through browser history.
    window.history.replaceState(
      {},
      "",
      window.location.pathname
    );

    verifyEmailOnce(token)
      .then(() => {
        setStatus("success");
        setMessage("Your email has been verified successfully!");
      })
      .catch((error) => {
        setStatus("error");
        setMessage(error.message);
      });
  }, []);

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        padding: "24px",
      }}
    >
      <section
        style={{
          maxWidth: "440px",
          width: "100%",
          padding: "32px",
          borderRadius: "12px",
          border: "1px solid #ddd",
          textAlign: "center",
          backgroundColor: "#fff",
          color: "#222",
        }}
      >
        <h1>Email Verification</h1>

        <p role="status">{message}</p>

        {status === "success" && (
          <p>
            You can now return to G-Sub and sign in.
          </p>
        )}

        {status !== "verifying" && (
          <a href="/">
            Return to G-Sub
          </a>
        )}
      </section>
    </main>
  );
}
