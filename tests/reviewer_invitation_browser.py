"""Isolated reviewer invitation browser test with synthetic workflow fixtures."""
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
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]

def wait_ready(process, url):
    for _ in range(150):
        if process.poll() is not None: raise RuntimeError(f'Server exited: {process.returncode}')
        try:
            with urllib.request.urlopen(url, timeout=1): return
        except OSError: time.sleep(.1)
    raise RuntimeError('Server timeout')

def main():
    with tempfile.TemporaryDirectory(prefix='reviewer-e2e-') as tmp:
        root = Path(tmp).resolve(); backend, frontend = free_port(), free_port()
        if backend == frontend: raise RuntimeError('port collision')
        origin = f'http://127.0.0.1:{frontend}'; base = f'http://127.0.0.1:{backend}'
        env = os.environ.copy(); env.update(REVIEWER_E2E_ROOT=str(root), REVIEWER_E2E_BACKEND_PORT=str(backend), REVIEWER_E2E_ORIGIN=origin, PYTHONPATH=str(PROJECT), VITE_API_PROXY_TARGET=base)
        processes = []
        with (root / 'servers.log').open('w+') as log:
            try:
                server = subprocess.Popen([str(PROJECT / '.venv/bin/python'), str(PROJECT / 'tests/reviewer_browser_server.py')], cwd=PROJECT, env=env, stdout=log, stderr=log, start_new_session=True); processes.append(server); wait_ready(server, base+'/health')
                vite = subprocess.Popen(['npm','run','dev','--','--host','127.0.0.1','--port',str(frontend),'--strictPort'], cwd=PROJECT/'frontend', env=env, stdout=log, stderr=log, start_new_session=True); processes.append(vite); wait_ready(vite, origin)
                fixture = json.loads((root/'fixtures.json').read_text()); mid = fixture['assignments']['round1']['manuscript_id']
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True); pages = {}
                    for role in ('eic','editor','other','third'):
                        context = browser.new_context(accept_downloads=True); page = context.new_page(); pages[role] = page
                        page.goto(origin); page.locator('#email').fill(f'{role}@example.com'); page.locator('#password').fill(fixture['password']); page.get_by_role('button', name='Sign In').click()
                        page.get_by_text('EIC Dashboard' if role == 'eic' else 'Editor Dashboard' if role == 'editor' else 'Reviewer Dashboard', exact=False).first.wait_for()
                    chief, editor, other, third = [pages[k] for k in ('eic','editor','other','third')]
                    chief.get_by_role('button', name=f'#{mid} Fixture round1').click()
                    panel = chief.get_by_role('region', name='Reviewer invitations'); panel.get_by_label('Reviewer ID').fill(str(fixture['user_ids']['other'])); panel.get_by_role('button', name='Invite reviewer').click(); panel.get_by_text('Reviewer invitation sent.').wait_for()
                    other.get_by_role('button', name='Refresh', exact=True).click(); card = other.locator('.reviewer-card').filter(has_text='Fixture round1'); card.get_by_text('Invitation: pending').wait_for()
                    assert card.get_by_role('button', name='Download Review File').count() == 0
                    assert card.get_by_role('button', name='Submit Review').count() == 0
                    invitation = other.context.request.get(base+'/assignments/my', headers={'Authorization':'Bearer '+fixture['tokens']['other']}).json()[0]; aid = invitation['assignment_id']
                    assert other.context.request.get(base+f'/assignments/{aid}/download', headers={'Authorization':'Bearer '+fixture['tokens']['other']}).status == 409
                    card.get_by_role('button', name='Decline review invitation').click(); card.get_by_text('Invitation: declined').wait_for()
                    editor.get_by_role('button', name='Manage Manuscript').first.click()
                    # Locate the specific existing round-one manuscript card.
                    target = editor.locator(f'#manuscript-{mid}')
                    if target.get_by_role('button', name='Manage Manuscript').count(): target.get_by_role('button', name='Manage Manuscript').click()
                    panel = target.get_by_role('region', name='Reviewer invitations'); panel.get_by_text('invitation: declined', exact=False).wait_for()
                    panel.get_by_label('Reviewer ID').fill(str(fixture['user_ids']['third'])); panel.get_by_role('button', name='Invite reviewer').click(); panel.get_by_text('Reviewer invitation sent.').wait_for(); panel.get_by_text('2 active reviewers', exact=False).wait_for()
                    third.get_by_role('button', name='Refresh', exact=True).click(); card = third.locator('.reviewer-card').filter(has_text='Fixture round1'); card.get_by_text('Invitation: pending').wait_for()
                    card.get_by_role('button', name='Accept review invitation').click(); card.get_by_text('Invitation: accepted').wait_for()
                    with third.expect_download() as dl: card.get_by_role('button', name='Download Review File').click()
                    assert Path(dl.value.path()).read_bytes() == b'%PDF-confirmed-initial-edit'
                    assert dl.value.suggested_filename == f'manuscript_{mid}_review_round_1.pdf'
                    card.get_by_label('Review Comments').fill('Synthetic review'); card.get_by_label('Recommendation').select_option('accept'); card.get_by_role('button', name='Submit Review').click(); card.get_by_text('Review already submitted.').wait_for()
                    browser.close(); print('PASS browser EIC invitation, reviewer decline, editor replacement, acceptance, bound PDF and review')
            finally:
                for process in reversed(processes):
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                for process in reversed(processes):
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()

if __name__ == '__main__': main()
