"""Browser check of the same-origin demo; requires Playwright and Chromium."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv/bin/python"

def main():
    with tempfile.TemporaryDirectory(prefix="demo-browser-") as tmp:
        root = Path(tmp).resolve() / "demo"
        initialized = subprocess.run([str(PYTHON), str(PROJECT / "scripts/init_demo.py"), "--data-root", str(root)], cwd=PROJECT, capture_output=True, text=True)
        if initialized.returncode:
            raise RuntimeError(initialized.stderr)
        accounts = json.loads((root / "accounts.json").read_text())
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen([str(PYTHON), str(PROJECT / "scripts/run_demo.py"), "--data-root", str(root), "--port", str(port)], cwd=PROJECT, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with urllib.request.urlopen(origin + "/api/health", timeout=1) as response:
                            if response.status == 200: break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("Demo startup timeout")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(accept_downloads=True)
                    page = context.new_page()
                    page.goto(origin)
                    page.locator("#email").fill(accounts["author"]["email"])
                    page.locator("#password").fill(accounts["author"]["password"])
                    page.get_by_role("button", name="Sign In").click()
                    page.get_by_text("Author Dashboard", exact=False).wait_for()
                    card = page.locator(".manuscript-card").filter(has_text="Synthetic Demo Manuscript")
                    card.wait_for()
                    with page.expect_download() as event:
                        card.get_by_role("button", name="Download File").click()
                    download = event.value
                    assert download.suggested_filename.endswith(".pdf")
                    assert Path(download.path()).read_bytes().startswith(b"%PDF-1.4")
                    assert context.request.get(origin + "/api/docs").status == 404
                    assert context.request.get(origin + "/api/openapi.json").status == 404
                    assert context.request.get(origin + "/api/health").status == 200
                    page.get_by_role("button", name="Logout").click()
                    page.locator("#email").fill(accounts["reviewer"]["email"])
                    page.locator("#password").fill(accounts["reviewer"]["password"])
                    page.get_by_role("button", name="Sign In").click()
                    page.get_by_text("Reviewer Dashboard", exact=False).wait_for()
                    page.get_by_text("No review assignments found.").wait_for()
                    page.get_by_role("button", name="Logout").click()
                    page.locator("#email").fill(accounts["eic"]["email"])
                    page.locator("#password").fill(accounts["eic"]["password"])
                    with page.expect_response(lambda response: response.url.endswith("/api/users/login") and response.request.method == "POST") as login_response:
                        page.get_by_role("button", name="Sign In").click()
                    assert login_response.value.status == 200
                    page.get_by_role("heading", name="EIC Dashboard").wait_for()
                    page.get_by_role("button", name="Logout").click()
                    page.route("**/api/users/login", lambda route: route.fulfill(status=401, content_type="text/plain", body="Unauthorized\n"))
                    page.locator("#email").fill(accounts["eic"]["email"])
                    page.locator("#password").fill(accounts["eic"]["password"])
                    page.get_by_role("button", name="Sign In").click()
                    page.get_by_text("Your protected tunnel session may have expired.", exact=False).wait_for()
                    page.unroute("**/api/users/login")
                    page.locator("#password").fill("synthetic-invalid-password")
                    with page.expect_response(lambda response: response.url.endswith("/api/users/login") and response.request.method == "POST") as invalid_response:
                        page.get_by_role("button", name="Sign In").click()
                    assert invalid_response.value.status == 401
                    assert "application/json" in invalid_response.value.headers.get("content-type", "")
                    page.get_by_text("Invalid email or password", exact=False).wait_for()
                    browser.close()
                    print("PASS isolated logins and distinction between tunnel plain-text and application JSON 401 responses")
            finally:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

if __name__ == "__main__": main()
