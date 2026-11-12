"""Isolated EIC browser workflow, with other participants driven by real APIs."""
import json
import sqlite3
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / '.venv/bin/python'


def main():
    with tempfile.TemporaryDirectory(prefix='eic-browser-') as tmp:
        root = Path(tmp).resolve() / 'demo'
        init = subprocess.run([str(PYTHON), str(PROJECT / 'scripts/init_demo.py'), '--data-root', str(root)], cwd=PROJECT, capture_output=True, text=True)
        if init.returncode: raise RuntimeError(init.stderr)
        accounts = json.loads((root / 'accounts.json').read_text())
        # Additional synthetic role fixture in this disposable database only.
        with sqlite3.connect(root / 'manuscript.db') as db:
            db.execute("INSERT INTO users (name,email,password_hash,role,created_at) SELECT 'Second reviewer', 'second-reviewer@example.com', password_hash, 'reviewer', datetime('now') FROM users WHERE role='reviewer' LIMIT 1")
            db.commit()
        accounts['reviewer2'] = {'email':'second-reviewer@example.com', 'password':accounts['reviewer']['password']}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with tempfile.TemporaryFile(mode='w+') as log:
            server = subprocess.Popen([str(PYTHON), str(PROJECT / 'scripts/run_demo.py'), '--data-root', str(root), '--port', str(port)], cwd=PROJECT, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if server.poll() is not None:
                        log.seek(0); raise RuntimeError(log.read())
                    try:
                        with urllib.request.urlopen(origin + '/api/health', timeout=1): break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('Server startup timeout')
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(); page = context.new_page(); api = context.request
                    def call(method, path, role, **kwargs):
                        result = getattr(api, method)(origin + '/api' + path, headers={'Authorization': 'Bearer ' + tokens[role]}, **kwargs)
                        assert result.ok, f'{method} {path}: {result.status} {result.text()}'
                        return result.json()
                    tokens = {}
                    for role in ('author', 'editor', 'reviewer', 'reviewer2', 'eic'):
                        login = api.post(origin + '/api/users/login', data=accounts[role]); assert login.ok
                        tokens[role] = login.json()['access_token']
                    draft = call('get', '/manuscripts/my', 'author')[0]; mid = draft['manuscript_id']
                    assert api.get(origin + f'/api/eic/manuscripts/{mid}/overview', headers={'Authorization':'Bearer '+tokens['eic']}).status == 404
                    call('post', f'/manuscripts/{mid}/submit', 'author')
                    page.goto(origin); page.locator('#email').fill(accounts['eic']['email']); page.locator('#password').fill(accounts['eic']['password']); page.get_by_role('button', name='Sign In').click()
                    page.get_by_role('heading', name='EIC Dashboard').wait_for()
                    page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click()
                    page.get_by_role('heading', name='Initial screening', exact=True).wait_for()
                    form = page.locator('form').filter(has_text='Initial screening')
                    form.get_by_label('Decision').select_option('pass'); form.get_by_label('Comments').fill('Suitable for editing'); form.get_by_role('button', name='Record screening').click()
                    page.get_by_role('heading', name='Invite initial editor').wait_for()
                    form = page.locator('form').filter(has_text='Invite initial editor'); form.get_by_label('Editor').select_option(label='Demo Editor'); form.get_by_role('button', name='Send invitation').click()
                    page.get_by_text('Demo Editor — pending').wait_for()
                    overview = call('get', f'/eic/manuscripts/{mid}/overview', 'eic'); inv = overview['invitations'][0]['invitation_id']
                    call('post', f'/eic/initial-editor/invitations/{inv}/respond', 'editor', data={'decision':'accept'})
                    submission = call('post', f'/eic/initial-editing/{mid}/submit', 'editor', multipart={'comments':'Edited', 'file':{'name':'edit.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4\nSynthetic edited'}})['submission_id']
                    for role, suffix in [('author','author-review'), ('editor','editor-approval')]:
                        call('post', f'/eic/initial-editing/submissions/{submission}/{suffix}', role, data={'decision':'approve','comments':'Approved'})
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click()
                    form = page.locator('form').filter(has_text='Review initial editing submission'); form.get_by_label('Decision').select_option('approve'); form.get_by_label('Comments').fill('Approved'); form.get_by_role('button', name='Record editing decision').click()
                    page.locator('main').get_by_text('pre review confirmation', exact=False).wait_for()
                    call('post', f'/eic/initial-editing/submissions/{submission}/pre-review-confirmation', 'author', data={'decision':'confirm','comments':'Confirmed'})
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click(); page.locator('main').get_by_text('Review progress: 0/2', exact=False).first.wait_for()
                    editor_page = context.new_page(); editor_page.goto(origin)
                    editor_page.locator('#email').fill(accounts['editor']['email']); editor_page.locator('#password').fill(accounts['editor']['password']); editor_page.get_by_role('button', name='Sign In').click()
                    editor_card = editor_page.locator(f'#manuscript-{mid}')
                    expect(editor_card.get_by_text('Decision review progress:', exact=False)).to_contain_text('0/2')
                    reviewer = call('get', '/users/me', 'reviewer')['user_id']
                    assignment = call('post', '/assignments/', 'editor', data={'manuscript_id':mid,'reviewer_id':reviewer,'review_round':1})['assignment_id']
                    call('post', f'/assignments/{assignment}/invitation-response', 'reviewer', data={'decision':'accept'})
                    call('post', '/reviews/', 'reviewer', data={'assignment_id':assignment,'comments':'Solid work', 'recommendation':'accept'})
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click(); page.get_by_text('Recommendation: accept — Solid work').wait_for()
                    page.locator('main').get_by_text('Review progress: 1/2', exact=False).first.wait_for()
                    editor_page.get_by_role('button', name='Refresh', exact=True).click()
                    expect(editor_card.get_by_text('Decision review progress:', exact=False)).to_contain_text('1/2')
                    assert page.locator('form').filter(has_text='Editorial decision for round').count() == 0
                    reviewer2 = call('get', '/users/me', 'reviewer2')['user_id']
                    second = call('post', '/assignments/', 'editor', data={'manuscript_id':mid,'reviewer_id':reviewer2,'review_round':1})['assignment_id']
                    call('post', f'/assignments/{second}/invitation-response', 'reviewer2', data={'decision':'accept'})
                    call('post', '/reviews/', 'reviewer2', data={'assignment_id':second,'comments':'Second review', 'recommendation':'accept'})
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click(); page.locator('main').get_by_text('Review progress: 2/2', exact=False).first.wait_for()
                    editor_page.get_by_role('button', name='Refresh', exact=True).click()
                    expect(editor_card.get_by_text('Decision review progress:', exact=False)).to_contain_text('2/2')
                    form = page.locator('form').filter(has_text='Editorial decision for round'); form.get_by_label('Decision').select_option('accept'); form.get_by_label('Comments').fill('Accepted'); form.get_by_role('button', name='Record editorial decision').click()
                    page.get_by_text('Round 1: accept — Accepted').wait_for(); page.locator('main').get_by_text('awaiting author agreement', exact=False).first.wait_for()
                    page.get_by_text('pending · version placeholder-v1', exact=False).wait_for()
                    author_page = context.new_page(); author_page.goto(origin)
                    author_page.locator('#email').fill(accounts['author']['email']); author_page.locator('#password').fill(accounts['author']['password']); author_page.get_by_role('button', name='Sign In').click()
                    agreement = author_page.get_by_role('region', name='Author agreements')
                    agreement.get_by_text('Prototype placeholder:', exact=False).wait_for()
                    agreement.get_by_role('button', name='Accept prototype agreement').click()
                    agreement.get_by_text('Status: accepted', exact=False).wait_for()
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click()
                    page.locator('main').get_by_text('final editing', exact=False).first.wait_for()
                    page.get_by_text('accepted · version placeholder-v1', exact=False).wait_for()
                    editor_page.get_by_role('button', name='Refresh', exact=True).click()
                    editor_card.get_by_text('Author agreement: accepted', exact=False).wait_for()
                    browser.close()
                    print('PASS EIC/author browser: review decision, prototype agreement acceptance, final editing')
            finally:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill(); server.wait()

if __name__ == '__main__': main()
