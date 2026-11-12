// Both the demo gateway and the local Vite proxy expose the API here.
export const API_URL = "/api";

// A response status confirms rejection only for ordinary client errors. Network
// errors, request timeouts and server errors may occur after a write was saved.
export async function submitAction(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
    error.confirmedRejection = response.status >= 400 && response.status < 500 && response.status !== 408;
    throw error;
  }
  return response;
}

export function actionFailureMessage(error) {
  return error.confirmedRejection
    ? `Action request rejected: ${error.message}`
    : "Action outcome could not be confirmed and may have been saved. Refresh the data to check before trying again.";
}
