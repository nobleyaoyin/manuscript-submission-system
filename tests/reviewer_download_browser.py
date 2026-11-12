"""Run with a Python installation containing Playwright and an installed Chromium.

Creates only temporary fixtures; exits without connecting to a pre-existing server.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def wait_ready(process, url):
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError(f"Test server exited: {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Test server did not become ready")

def main():
    with tempfile.TemporaryDirectory(prefix="reviewer-e2e-") as temporary:
        root = Path(temporary).resolve()
        backend_port, frontend_port = free_port(), free_port()
        if backend_port == frontend_port:
            raise RuntimeError("Port collision")
        origin = f"http://127.0.0.1:{frontend_port}"
        env = os.environ.copy()
        env.update(REVIEWER_E2E_ROOT=str(root), REVIEWER_E2E_BACKEND_PORT=str(backend_port), REVIEWER_E2E_ORIGIN=origin, PYTHONPATH=str(PROJECT), VITE_API_PROXY_TARGET=f"http://127.0.0.1:{backend_port}")
        processes = []
        with (root / "server.log").open("w+") as log:
            try:
                backend = subprocess.Popen([str(PROJECT / ".venv/bin/python"), str(PROJECT / "tests/reviewer_browser_server.py")], cwd=PROJECT, env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(backend)
                wait_ready(backend, f"http://127.0.0.1:{backend_port}/health")
                frontend = subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"], cwd=PROJECT / "frontend", env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(frontend)
                wait_ready(frontend, origin)
                fixture = json.loads((root / "fixtures.json").read_text())
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(accept_downloads=True)
                    page = context.new_page()
                    page.route("http://127.0.0.1:8000/**", lambda route: route.continue_(url=route.request.url.replace("http://127.0.0.1:8000/", f"http://127.0.0.1:{backend_port}/", 1)))
                    page.goto(origin)
                    page.locator("#email").fill("reviewer@example.com")
                    page.locator("#password").fill(fixture["password"])
                    page.get_by_role("button", name="Sign In").click()
                    try:
                        page.get_by_text("Reviewer Dashboard", exact=False).wait_for(timeout=10000)
                    except Exception:
                        print("Login page text:", page.locator("body").inner_text(), flush=True)
                        raise
                    for label in ("round1", "pdf", "docx", "doc"):
                        info = fixture["assignments"][label]
                        card = page.locator(f"#assignment-{info['id']}")
                        card.wait_for()
                        with page.expect_download() as event:
                            card.get_by_role("button", name="Download Review File").click()
                        download = event.value
                        extension = "pdf" if label == "round1" else label
                        assert download.suggested_filename == f"manuscript_{info['manuscript_id']}_review_round_{info['round']}.{extension}", download.suggested_filename
                        expected = root / (f"uploads/initial_editing/{info['manuscript_id']}/confirmed.pdf" if label == "round1" else f"uploads/manuscripts/{info['manuscript_id']}/revised.{label}")
                        assert Path(download.path()).read_bytes() == expected.read_bytes(), label
                        print(f"PASS browser download {label}: correct file and filename", flush=True)
                    legacy = page.locator(f"#assignment-{fixture['assignments']['legacy']['id']}")
                    assert legacy.get_by_text("Review file unavailable. Contact the editorial team.").is_visible()
                    assert legacy.get_by_role("button", name="Download Review File").count() == 0
                    print("PASS browser legacy assignment unavailable", flush=True)
                    assignment = fixture["assignments"]["round1"]
                    base = f"http://127.0.0.1:{backend_port}"
                    def get(role, path):
                        return context.request.get(base + path, headers={"Authorization": "Bearer " + fixture["tokens"][role]})
                    assert get("other", f"/assignments/{assignment['id']}/download").status == 403
                    assert get("author", f"/assignments/{assignment['id']}/download").status == 403
                    assert context.request.get(base + f"/assignments/{assignment['id']}/download").status == 401
                    assert get("other", "/assignments/my").json() == []
                    for suffix in ("download", "versions", "versions/original.pdf"):
                        assert get("reviewer", f"/manuscripts/{assignment['manuscript_id']}/{suffix}").status == 403
                    for role in ("author", "editor", "eic"):
                        for suffix in ("download", "versions", "versions/original.pdf"):
                            response = get(role, f"/manuscripts/{assignment['manuscript_id']}/{suffix}")
                            assert response.status == 200, (role, suffix, response.status, response.text())
                            if suffix == "download":
                                assert response.body() == b"%PDF-original-fixture"
                        print(f"PASS {role} original download and version history", flush=True)
                    print("PASS reviewer assignment isolation and direct-file restrictions", flush=True)
                    browser.close()
            finally:
                for process in reversed(processes):
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                for process in reversed(processes):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

if __name__ == "__main__":
    main()
