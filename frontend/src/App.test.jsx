/**
 * Email verification resend UI tests.
 * All network requests are mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import App from "./App";

const EMAIL = "unverified-test@example.com";
const PASSWORD = "TestPassword123!";

function mockResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: () => "application/json",
    },
    json: async () => body,
  };
}

async function openLogin(user) {
  render(<App />);

  // Select the Author role first.
  const roleButton = await screen.findByRole("button", {
    name: /author/i,
  });

  await user.click(roleButton);

  await user.type(
    screen.getByLabelText(/email address/i),
    EMAIL
  );

  await user.type(
    screen.getByLabelText(/^password/i),
    PASSWORD
  );

  await user.click(
    screen.getByRole("button", {
      name: /^sign in$/i,
    })
  );

  expect(
    await screen.findByText(
      /please verify your email address before signing in/i
    )
  ).toBeTruthy();

  return screen.findByRole("button", {
    name: /resend verification email/i,
  });
}

describe("Resend Verification UI", () => {
  beforeEach(() => {
    sessionStorage.clear();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("/users/login")) {
          return mockResponse(403, {
            detail:
              "Please verify your email address before signing in.",
          });
        }

        throw new Error(`Unexpected API request: ${url}`);
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows Resend button after unverified login", async () => {
    const user = userEvent.setup();

    const button = await openLogin(user);

    expect(button).toBeTruthy();
  });

  it("sends email and password to resend endpoint", async () => {
    const user = userEvent.setup();

    const fetchMock = vi.mocked(fetch);

    fetchMock.mockImplementation(async (url) => {
      if (String(url).includes("/users/login")) {
        return mockResponse(403, {
          detail:
            "Please verify your email address before signing in.",
        });
      }

      if (String(url).includes("/users/resend-verification")) {
        return mockResponse(200, {
          message: "Verification email sent",
          sent: true,
        });
      }

      throw new Error(`Unexpected API request: ${url}`);
    });

    const button = await openLogin(user);
    await user.click(button);

    const resendCall = fetchMock.mock.calls.find(
      ([url]) => String(url).includes("/users/resend-verification")
    );

    expect(resendCall).toBeDefined();
    expect(resendCall[1].method).toBe("POST");
    expect(JSON.parse(resendCall[1].body)).toEqual({
      email: EMAIL,
      password: PASSWORD,
    });
  });

  it("keeps Resend button after HTTP 429", async () => {
    const user = userEvent.setup();

    vi.mocked(fetch).mockImplementation(async (url) => {
      if (String(url).includes("/users/login")) {
        return mockResponse(403, {
          detail:
            "Please verify your email address before signing in.",
        });
      }

      if (String(url).includes("/users/resend-verification")) {
        return mockResponse(429, {
          detail:
            "Please wait before requesting another verification email",
        });
      }

      throw new Error(`Unexpected API request: ${url}`);
    });

    const button = await openLogin(user);
    await user.click(button);

    expect(
      await screen.findByText(
        /please wait before requesting another verification email/i
      )
    ).toBeTruthy();

    expect(
      screen.getByRole("button", {
        name: /resend verification email/i,
      })
    ).toBeTruthy();
  });

  it("shows success message after resend", async () => {
    const user = userEvent.setup();

    vi.mocked(fetch).mockImplementation(async (url) => {
      if (String(url).includes("/users/login")) {
        return mockResponse(403, {
          detail:
            "Please verify your email address before signing in.",
        });
      }

      if (String(url).includes("/users/resend-verification")) {
        return mockResponse(200, {
          message: "Verification email sent",
          sent: true,
        });
      }

      throw new Error(`Unexpected API request: ${url}`);
    });

    const button = await openLogin(user);
    await user.click(button);

    expect(
      await screen.findByText(
        /verification email sent. please check your inbox/i
      )
    ).toBeTruthy();
  });
});
