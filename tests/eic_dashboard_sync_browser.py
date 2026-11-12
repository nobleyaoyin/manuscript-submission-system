"""Isolated browser checks for refreshing an open EIC dashboard."""
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from author_agreement_panel_sync_browser import free_port, prepare_pending, wait_ready

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='reviewer-e2e-') as temporary:
        root = Path(temporary).resolve()
        backend, frontend = free_port(), free_port()
        if backend == frontend:
            raise RuntimeError('Port collision')
        base, origin = f'http://127.0.0.1:{backend}', f'http://127.0.0.1:{frontend}'
        env = os.environ.copy()
        env.update(REVIEWER_E2E_ROOT=str(root), REVIEWER_E2E_BACKEND_PORT=str(backend), REVIEWER_E2E_ORIGIN=origin, PYTHONPATH=str(PROJECT), VITE_API_PROXY_TARGET=base)
        processes = []
        with (root / 'server.log').open('w+') as log:
            try:
                server = subprocess.Popen([str(PROJECT / '.venv/bin/python'), str(PROJECT / 'tests/reviewer_browser_server.py')], cwd=PROJECT, env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(server); wait_ready(server, base + '/health')
                fixture = json.loads((root / 'fixtures.json').read_text())
                pending = prepare_pending(root, fixture)  # Synthetic previous EIC decisions; no EIC Accept request.
                vite = subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', str(frontend), '--strictPort'], cwd=PROJECT / 'frontend', env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(vite); wait_ready(vite, origin)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(); page = context.new_page(); api = context.request
                    def headers(role): return {'Authorization': 'Bearer ' + fixture['tokens'][role]}
                    def choose(label): page.get_by_role('button', name=f"#{pending[label]['manuscript_id']} Fixture {label}").click()
                    def respond(label, decision):
                        response = api.post(base + f"/agreements/{pending[label]['agreement_id']}/respond", headers=headers('author'), data={'decision':decision, 'agreement_version':'placeholder-v1'})
                        assert response.status == 200, response.status
                    page.goto(origin); page.locator('#email').fill('eic@example.com'); page.locator('#password').fill(fixture['password']); page.get_by_role('button', name='Sign In').click()
                    page.get_by_role('heading', name='EIC Dashboard').wait_for()
                    choose('round1'); main = page.locator('main')
                    main.get_by_text('pending · version placeholder-v1', exact=False).wait_for()
                    respond('round1', 'accept')
                    expect(main.get_by_text('pending · version placeholder-v1', exact=False)).to_be_visible()
                    page.get_by_role('button', name='Refresh dashboard').click()
                    main.get_by_text('accepted · version placeholder-v1', exact=False).wait_for()
                    expect(main).to_contain_text('Status: final editing')
                    expect(page.get_by_role('button', name=f"#{pending['round1']['manuscript_id']} Fixture round1")).to_have_attribute('aria-pressed','true')
                    form=main.locator('form').filter(has_text='Invite final editor')
                    form.get_by_label('Editor').select_option(label='Editor')
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                    expect(form.get_by_label('Editor')).to_have_value(str(fixture['user_ids']['editor']))
                    print('PASS manual refresh: agreement, manuscript status, invitation control, selection and unsent selection')

                    choose('pdf'); main.get_by_text('pending · version placeholder-v1', exact=False).wait_for()
                    respond('pdf','decline')
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                    main.get_by_text('declined · version placeholder-v1', exact=False).wait_for()
                    expect(main).to_contain_text('author agreement declined')
                    expect(main.locator('form').filter(has_text='Invite final editor')).to_have_count(0)
                    print('PASS focus refresh: declined agreement and held workflow')

                    choose('round1'); main.get_by_text('accepted · version placeholder-v1', exact=False).wait_for()
                    mid = pending['round1']['manuscript_id']
                    invitation=api.post(base+'/final-workflow/invite',headers=headers('eic'),data={'manuscript_id':mid,'editor_id':fixture['user_ids']['editor']})
                    assert invitation.status == 201, invitation.status
                    iid=invitation.json()['invitation_id']
                    response=api.post(base+f'/final-workflow/invitations/{iid}/respond',headers=headers('editor'),data={'decision':'accept'})
                    assert response.status == 200, response.status
                    response=api.post(base+f'/eic/final-editing/{mid}/submit',headers=headers('editor'),multipart={'comments':'Synthetic final edit','file':{'name':'final.pdf','mimeType':'application/pdf','buffer':b'%PDF-synthetic-final'}})
                    assert response.status == 201, response.status
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                    main.get_by_text('Next: EIC reviews the final PDF.',exact=False).wait_for()
                    main.get_by_role('heading',name='Review final edit #1').wait_for()
                    print('PASS focus refresh: final-editor invitation and pending EIC review')

                    page.route(f'**/api/eic/manuscripts/{mid}/overview', lambda route: route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic refresh failure"}'))
                    page.get_by_role('button', name='Refresh dashboard').click()
                    expect(page.get_by_role('alert')).to_contain_text('Refresh failed')
                    expect(main.get_by_role('heading',name=f'#{mid} Fixture round1',level=2)).to_be_visible()
                    page.unroute(f'**/api/eic/manuscripts/{mid}/overview')
                    page.get_by_role('button', name='Refresh dashboard').click()
                    expect(page.get_by_role('alert')).to_have_count(0)
                    main.get_by_role('heading',name='Review final edit #1').wait_for()
                    print('PASS refresh failure shown; selection retained; retry succeeds')

                    # Delay an older overview response, then switch selection before it completes.
                    slow = pending['round1']['manuscript_id']
                    page.evaluate('''mid => {const original = window.fetch; window.fetch = async (...args) => {const response = await original(...args); if (String(args[0]).includes(`/eic/manuscripts/${mid}/overview`)) await new Promise(resolve => setTimeout(resolve, 800)); return response;};}''', slow)
                    choose('round1'); choose('pdf')
                    expected = f"#{pending['pdf']['manuscript_id']} Fixture pdf"
                    main.get_by_role('heading',name=expected,level=2).wait_for()
                    page.wait_for_timeout(1000)
                    expect(main.get_by_role('heading',name=expected,level=2)).to_be_visible()
                    print('PASS delayed older response cannot overwrite the selected manuscript')
                    browser.close()
            finally:
                for process in reversed(processes):
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                for process in reversed(processes):
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()


if __name__ == '__main__': main()
