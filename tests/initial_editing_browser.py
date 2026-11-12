"""Exercise Stage 3 entirely through role dashboards in a disposable demo."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / '.venv/bin/python'
PDF1 = b'%PDF-1.4\nsynthetic first edit'
PDF2 = b'%PDF-1.4\nsynthetic revised edit'


def main():
    with tempfile.TemporaryDirectory(prefix='stage3-browser-') as tmp:
        root = Path(tmp).resolve() / 'demo'
        init = subprocess.run([str(PYTHON), str(PROJECT / 'scripts/init_demo.py'), '--data-root', str(root)], cwd=PROJECT, capture_output=True, text=True)
        if init.returncode: raise RuntimeError(init.stderr)
        accounts = json.loads((root / 'accounts.json').read_text())
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
                    pages = {}
                    for role in ('author', 'editor', 'eic'):
                        ctx = browser.new_context(accept_downloads=True); page = ctx.new_page(); pages[role] = page
                        page.goto(origin); page.locator('#email').fill(accounts[role]['email']); page.locator('#password').fill(accounts[role]['password']); page.get_by_role('button', name='Sign In').click()
                        if role == 'eic': page.get_by_role('heading', name='EIC Dashboard').wait_for()
                        else: page.get_by_role('region', name='Initial editing workflow').wait_for()
                    author, editor, chief = pages['author'], pages['editor'], pages['eic']
                    author.on('dialog', lambda dialog: dialog.accept())
                    # The initializer creates the draft; submit it through the Author dashboard.
                    author.locator('.manuscript-card').get_by_role('button', name='Submit Manuscript').click()
                    author.locator('.manuscript-card').get_by_text('submitted', exact=True).wait_for()
                    chief.reload(); chief.get_by_role('button', name='#1 Synthetic Demo Manuscript').click()
                    form = chief.locator('form').filter(has_text='Initial screening'); form.get_by_label('Decision').select_option('pass'); form.get_by_label('Comments').fill('Suitable'); form.get_by_role('button', name='Record screening').click()
                    def invite():
                        form = chief.locator('form').filter(has_text='Invite initial editor'); form.get_by_label('Editor').select_option(label='Demo Editor'); form.get_by_role('button', name='Send invitation').click()
                        chief.get_by_text('Demo Editor — pending').wait_for()
                    invite()
                    editor.get_by_role('button', name='Refresh initial editing').click()
                    epanel = editor.get_by_role('region', name='Initial editing workflow')
                    epanel.get_by_text('Invited editor: accept or decline', exact=False).wait_for()
                    epanel.get_by_role('button', name='Decline invitation').click(); epanel.get_by_text('No initial editing items.').wait_for()
                    chief.reload(); chief.get_by_role('button', name='#1 Synthetic Demo Manuscript').click(); invite()
                    editor.get_by_role('button', name='Refresh initial editing').click(); epanel.get_by_role('button', name='Accept invitation').click()
                    epanel.get_by_text('Assigned editor: upload an initial-editing PDF.').wait_for()
                    def upload(content, note):
                        form = epanel.locator('form').filter(has_text='Upload initial-editing PDF')
                        form.get_by_label('PDF file').set_input_files({'name':'edited.pdf', 'mimeType':'application/pdf', 'buffer':content})
                        form.get_by_label('Editing comments').fill(note); form.get_by_role('button', name='Submit initial-editing PDF').click()
                        epanel.get_by_text('Author: review the initial-editing PDF.').wait_for()
                    upload(PDF1, 'First edit')
                    apanel = author.get_by_role('region', name='Initial editing workflow'); apanel.get_by_role('button', name='Refresh initial editing').click()
                    apanel.get_by_text('Author: review the initial-editing PDF.').wait_for()
                    with author.expect_download() as dl:
                        apanel.get_by_role('button', name='Download initial-editing PDF').click()
                    assert Path(dl.value.path()).read_bytes() == PDF1 and dl.value.suggested_filename.endswith('.pdf')
                    form = apanel.locator('form').filter(has_text='Review initial editing'); form.get_by_label('Decision').select_option('request_revision'); form.get_by_label('Comments').fill('Please revise'); form.get_by_role('button', name='Submit author review').click()
                    apanel.get_by_text('Assigned editor: upload an initial-editing PDF.').wait_for()
                    epanel.get_by_role('button', name='Refresh initial editing').click(); upload(PDF2, 'Revised edit')
                    apanel.get_by_role('button', name='Refresh initial editing').click(); apanel.get_by_text('Author: review the initial-editing PDF.').wait_for()
                    with author.expect_download() as dl:
                        apanel.get_by_role('button', name='Download initial-editing PDF').first.click()
                    assert Path(dl.value.path()).read_bytes() == PDF2
                    form = apanel.locator('form').filter(has_text='Review initial editing'); form.get_by_label('Decision').select_option('approve'); form.get_by_label('Comments').fill('Looks good'); form.get_by_role('button', name='Submit author review').click()
                    apanel.get_by_text('Assigned editor: review and approve', exact=False).wait_for()
                    epanel.get_by_role('button', name='Refresh initial editing').click()
                    form = epanel.locator('form').filter(has_text='Editor approval'); form.get_by_label('Decision').select_option('approve'); form.get_by_label('Comments').fill('Approved'); form.get_by_role('button', name='Submit editor approval').click()
                    epanel.get_by_text('EIC: review the initial-editing PDF.').wait_for()
                    chief.reload(); chief.get_by_role('button', name='#1 Synthetic Demo Manuscript').click()
                    form = chief.locator('form').filter(has_text='Review initial editing submission'); form.get_by_label('Decision').select_option('approve'); form.get_by_label('Comments').fill('Approved'); form.get_by_role('button', name='Record editing decision').click()
                    chief.locator('main').get_by_text('pre review confirmation', exact=False).wait_for()
                    apanel.get_by_role('button', name='Refresh initial editing').click()
                    form = apanel.locator('form').filter(has_text='Confirm pre-review version'); form.get_by_label('Decision').select_option('confirm'); form.get_by_label('Comments').fill('Confirmed'); form.get_by_role('button', name='Submit confirmation').click()
                    apanel.get_by_text('Assigned editor: proceed with reviewer assignment.').wait_for()
                    browser.close(); print('PASS Stage 3 browser: decline, accept, two uploads, revision, downloads, author/editor/EIC approval and confirmation')
            finally:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill(); server.wait()

if __name__ == '__main__': main()
