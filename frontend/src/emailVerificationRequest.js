import { API_URL } from "./api";

// Share in-flight requests across React StrictMode effect re-runs.
// Do not persist verification tokens in browser storage.
const pendingRequests = new Map();

export function verifyEmailOnce(token) {
  if (pendingRequests.has(token)) {
    return pendingRequests.get(token);
  }

  const request = fetch(`${API_URL}/users/verify-email`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ token }),
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(
        response.status === 400
          ? "This verification link is invalid, expired, or already used."
          : "Unable to verify your email. Please try again later."
      );
    }

    return response.json();
  });

  pendingRequests.set(token, request);

  return request;
}
