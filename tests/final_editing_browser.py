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
            db.execute("INSERT INTO users (name,email,password_hash,role,created_at) SELECT 'Second Editor', 'second-editor@example.com', password_hash, 'editor', datetime('now') FROM users WHERE role='editor' LIMIT 1")
            db.commit()
        accounts['editor2'] = {'email':'second-editor@example.com', 'password':accounts['editor']['password']}
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
                    # Stage 7 actions below all use real dashboard controls and synthetic files.
                    form = page.locator('form').filter(has_text='Invite final editor')
                    form.get_by_label('Editor').select_option(label='Second Editor')
                    form.get_by_role('button', name='Send final editor invitation').click()
                    second_editor = context.new_page(); second_editor.goto(origin)
                    second_editor.locator('#email').fill(accounts['editor2']['email']); second_editor.locator('#password').fill(accounts['editor2']['password']); second_editor.get_by_role('button', name='Sign In').click()
                    second_panel = second_editor.get_by_role('region', name='Final editing workflow')
                    second_panel.get_by_role('button', name='Decline final editor invitation').click()
                    second_panel.get_by_text('Final editor invitation: declined').wait_for()
                    page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click()
                    form = page.locator('form').filter(has_text='Invite final editor')
                    form.get_by_label('Editor').select_option(label='Demo Editor'); form.get_by_role('button', name='Send final editor invitation').click()
                    panel = editor_page.get_by_role('region', name='Final editing workflow')
                    panel.get_by_role('button', name='Refresh final editing').click()
                    panel.get_by_role('button', name='Accept final editor invitation').click()
                    panel.get_by_text('Final editor invitation: accepted').wait_for()
                    def upload_final(content):
                        form = panel.locator('form').filter(has_text='Upload final editing PDF')
                        form.get_by_label('PDF file').set_input_files({'name':'final.pdf','mimeType':'application/pdf','buffer':content})
                        form.get_by_label('Editing comments').fill('Synthetic final edit')
                        form.get_by_role('button', name='Submit final editing PDF').click()
                        panel.get_by_text('EIC: review the final PDF.').wait_for()
                    def eic_final(decision):
                        page.reload(); page.get_by_role('button', name=f'#{mid} Synthetic Demo Manuscript').click()
                        form = page.locator('form').filter(has_text='Review final edit #')
                        form.get_by_label('Decision').select_option(decision); form.get_by_label('Comments').fill('Synthetic EIC review')
                        form.get_by_role('button', name='Submit final edit review').click()
                        page.get_by_text('Action completed.').first.wait_for()
                    upload_final(b'%PDF-1.4\nfinal version one')
                    eic_final('request_revision')
                    panel.get_by_role('button', name='Refresh final editing').click()
                    upload_final(b'%PDF-1.4\nfinal version two')
                    eic_final('approve')
                    author_panel = author_page.get_by_role('region', name='Final editing workflow')
                    author_panel.get_by_role('button', name='Refresh final editing').click()
                    expect(author_panel.get_by_role('button', name='Download final editing PDF')).to_have_count(2)
                    author_form = author_panel.locator('form').filter(has_text='Review final edits')
                    author_form.get_by_label('Decision').select_option('request_revision'); author_form.get_by_label('Comments').fill('Synthetic author corrections')
                    author_form.get_by_role('button', name='Submit final edit confirmation').click()
                    author_panel.get_by_text('Final editor: upload a PDF.').wait_for()
                    panel.get_by_role('button', name='Refresh final editing').click()
                    upload_final(b'%PDF-1.4\nfinal version three')
                    eic_final('approve')
                    author_panel.get_by_role('button', name='Refresh final editing').click()
                    expect(author_panel.get_by_role('button', name='Download final editing PDF')).to_have_count(3)
                    author_form = author_panel.locator('form').filter(has_text='Review final edits')
                    with author_page.expect_download() as download_info:
                        author_panel.get_by_role('button', name='Download final editing PDF').first.click()
                    assert Path(download_info.value.path()).read_bytes() == b'%PDF-1.4\nfinal version three'
                    assert download_info.value.suggested_filename.endswith('.pdf')
                    author_form.get_by_label('Decision').select_option('approve'); author_form.get_by_label('Comments').fill('Final edits confirmed')
                    author_form.get_by_role('button', name='Submit final edit confirmation').click()
                    author_panel.get_by_text('Final edits confirmed; EIC may invite a formatter.').wait_for()
                    browser.close()
                    print('PASS Stage 7 browser: decline, replacement, uploads, both correction loops, final approval and PDF download')
            finally:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill(); server.wait()

if __name__ == '__main__': main()
