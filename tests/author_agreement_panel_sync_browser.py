"""Exercise agreement synchronization with a disposable backend and synthetic fixtures."""
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_ready(process, url):
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError('Temporary server exited')
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Temporary server startup timeout')


def prepare_pending(root, fixture):
    """Seed only the disposable fixture DB; do not exercise EIC Accept."""
    path = root / 'browser.sqlite'
    if not root.name.startswith('reviewer-e2e-') or not path.is_file():
        raise RuntimeError('Refusing a non-temporary fixture database')
    result = {}
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for label in ('round1', 'pdf', 'docx', 'doc', 'legacy'):
            mid = fixture['assignments'][label]['manuscript_id']
            round_no = fixture['assignments'][label]['round']
            db.execute("UPDATE manuscripts SET status='awaiting_author_agreement' WHERE manuscript_id=?", (mid,))
            decision = db.execute("INSERT INTO eic_decisions (manuscript_id, eic_id, review_round, decision, comments, decided_at) VALUES (?, ?, ?, 'accept', 'Synthetic prior decision', datetime('now'))", (mid, fixture['user_ids']['eic'], round_no)).lastrowid
            aid = db.execute("INSERT INTO author_agreements (manuscript_id, decision_id, author_id, agreement_version, agreement_text, status, created_at) VALUES (?, ?, ?, 'placeholder-v1', 'Synthetic prototype agreement', 'pending', datetime('now'))", (mid, decision, fixture['user_ids']['author'])).lastrowid
            result[label] = {'manuscript_id': mid, 'agreement_id': aid}
        db.commit()
    return result


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
                processes.append(server)
                wait_ready(server, base + '/health')
                fixture = json.loads((root / 'fixtures.json').read_text())
                pending = prepare_pending(root, fixture)
                vite = subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', str(frontend), '--strictPort'], cwd=PROJECT / 'frontend', env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(vite)
                wait_ready(vite, origin)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context()
                    page = context.new_page()
                    page.goto(origin)
                    page.locator('#email').fill('author@example.com')
                    page.locator('#password').fill(fixture['password'])
                    page.get_by_role('button', name='Sign In').click()
                    agreements = page.get_by_role('region', name='Author agreements')
                    finals = page.get_by_role('region', name='Final editing workflow')
                    expect(agreements.locator('article')).to_have_count(5)
                    expect(finals.get_by_text('No final editing items.')).to_be_visible()
                    def agreement(label):
                        return agreements.locator('article').filter(has=page.get_by_role('heading', name=f"#{pending[label]['manuscript_id']} Fixture {label}", exact=True))
                    def manuscript(label):
                        return page.locator(f"#manuscript-{pending[label]['manuscript_id']}")
                    def final(label):
                        return finals.locator('article').filter(has=page.get_by_role('heading', name=f"#{pending[label]['manuscript_id']} Fixture {label}", exact=True))

                    agreement('round1').get_by_role('button', name='Accept prototype agreement').click()
                    expect(agreement('round1')).to_contain_text('Status: accepted')
                    expect(manuscript('round1')).to_contain_text('final editing')
                    expect(final('round1')).to_contain_text('EIC: invite a final editor.')
                    assert agreement('round1').get_by_role('button', name='Accept prototype agreement').count() == 0
                    repeat = context.request.post(base + f"/agreements/{pending['round1']['agreement_id']}/respond", headers={'Authorization': 'Bearer ' + fixture['tokens']['author']}, data={'decision':'accept', 'agreement_version':'placeholder-v1'})
                    assert repeat.status == 409, repeat.status
                    print('PASS accepted agreement, automatic manuscript/final panel refresh, duplicate rejected')

                    # Verify decline invokes both independent refreshes, even when final items remain empty.
                    with page.expect_response(lambda r: '/api/final-workflow/my' in r.url and r.request.method == 'GET') as final_refresh:
                        agreement('pdf').get_by_role('button', name='Decline prototype agreement').click()
                    assert final_refresh.value.ok
                    expect(agreement('pdf')).to_contain_text('Status: declined')
                    expect(agreement('pdf')).to_contain_text('Final editing is on hold')
                    expect(manuscript('pdf')).to_contain_text('author agreement declined')
                    assert final('pdf').count() == 0
                    print('PASS declined agreement and automatic manuscript/final panel refresh')

                    # Fail final-panel GET after the POST has been saved.
                    page.route('**/api/final-workflow/my', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
                    agreement('docx').get_by_role('button', name='Accept prototype agreement').click()
                    expect(agreement('docx')).to_contain_text('Status: accepted')
                    expect(agreements.get_by_role('alert')).to_contain_text('Response saved, but refresh failed')
                    expect(agreements.get_by_role('alert')).to_contain_text('do not submit the response again')
                    assert agreement('docx').get_by_role('button', name='Accept prototype agreement').count() == 0
                    expect(manuscript('docx')).to_contain_text('final editing')
                    page.unroute('**/api/final-workflow/my')
                    finals.get_by_role('button', name='Refresh final editing').click()
                    expect(final('docx')).to_contain_text('EIC: invite a final editor.')
                    print('PASS saved response survives failed final-panel refresh without retry controls')

                    # Fail manuscript GET after a successful response; final panel still refreshes.
                    page.route('**/api/manuscripts/my', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
                    agreement('doc').get_by_role('button', name='Accept prototype agreement').click()
                    expect(agreement('doc')).to_contain_text('Status: accepted')
                    expect(agreements.get_by_role('alert')).to_contain_text('Response saved, but refresh failed')
                    expect(final('doc')).to_contain_text('EIC: invite a final editor.')
                    assert agreement('doc').get_by_role('button', name='Accept prototype agreement').count() == 0
                    page.unroute('**/api/manuscripts/my')
                    print('PASS saved response reported when manuscript refresh fails; final panel still updates')

                    # A failed POST must not be presented as a saved response.
                    page.route(f"**/api/agreements/{pending['legacy']['agreement_id']}/respond", lambda route: route.fulfill(status=500, content_type='application/json', body='{"detail":"Synthetic response failure"}'))
                    agreement('legacy').get_by_role('button', name='Accept prototype agreement').click()
                    expect(agreements.get_by_role('alert')).to_contain_text('Could not confirm the agreement response')
                    expect(agreement('legacy')).to_contain_text('Status: pending')
                    assert agreement('legacy').get_by_role('button', name='Accept prototype agreement').count() == 1
                    print('PASS failed POST remains distinct from a saved response')
                    page.unroute(f"**/api/agreements/{pending['legacy']['agreement_id']}/respond")
                    page.route('**/api/agreements/my', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
                    agreement('legacy').get_by_role('button', name='Decline prototype agreement').click()
                    expect(agreement('legacy')).to_contain_text('Status: declined')
                    expect(agreements.get_by_role('alert')).to_contain_text('Response saved, but refresh failed')
                    assert agreement('legacy').get_by_role('button', name='Decline prototype agreement').count() == 0
                    page.unroute('**/api/agreements/my')
                    agreements.get_by_role('button', name='Refresh agreements').click()
                    expect(agreement('legacy')).to_contain_text('Status: declined')
                    print('PASS saved decline survives failed agreement-list refresh without retry controls')
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
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()


if __name__ == '__main__':
    main()
