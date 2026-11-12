"""Isolated browser checks: every API request is intercepted, no database is used."""
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    role = {'value': 'author'}
    calls = []
    with tempfile.TemporaryDirectory(prefix='gsub-role-test-') as temp:
        with (Path(temp) / 'vite.log').open('w') as log:
            process = subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=PROJECT / 'frontend', stdout=log, stderr=log, start_new_session=True)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Vite exited before startup')
                    try:
                        with urllib.request.urlopen(origin, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Vite startup timed out')
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page()

                    def api(route):
                        req = route.request
                        path = req.url.split('/api', 1)[1].split('?', 1)[0]
                        calls.append((req.method, path))
                        if path == '/users/login':
                            payload = json.loads(req.post_data)
                            assert set(payload) == {'email', 'password'}
                            if payload['password'] == 'wrong':
                                route.fulfill(status=401, content_type='application/json', body=json.dumps({'detail': 'Invalid email or password'}))
                                return
                            body = {'access_token': 'synthetic-token', 'token_type': 'bearer'}
                        elif path == '/users/me':
                            body = {'user_id': 1, 'name': 'Synthetic User', 'email': 'test@example.invalid', 'role': role['value']}
                        elif path == '/users/register':
                            assert set(json.loads(req.post_data)) == {'name', 'email', 'password'}
                            body = {'user_id': 2}
                        elif req.method != 'GET':
                            route.fulfill(status=403, body='Unexpected write blocked')
                            return
                        elif path == '/notifications/unread-count':
                            body = {'unread_count': 0}
                        else:
                            body = []
                        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))

                    page.route('**/api/**', api)
                    page.goto(origin)
                    expect(page.get_by_role('heading', name='G-Sub.')).to_be_visible()
                    roles = ['Editor-in-Chief', 'Editor', 'Peer Reviewer', 'Author / Submitter']
                    for label in roles:
                        page.get_by_role('button', name=label, exact=True).click()
                        expect(page.locator('.auth-brand')).to_have_text('G-Sub.')
                        expect(page.get_by_text(f'Selected role: {label}')).to_be_visible()
                        expect(page.get_by_label('Email Address')).to_be_visible()
                        page.get_by_role('button', name='Choose a different role').click()
                    # Keyboard focus is visible and Enter activates a card.
                    page.keyboard.press('Tab')
                    assert page.evaluate("document.activeElement.classList.contains('role-card')")
                    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != 'none'
                    page.keyboard.press('Enter')
                    expect(page.get_by_text('Selected role: Editor-in-Chief')).to_be_visible()
                    page.get_by_label('Email Address').fill('test@example.invalid')
                    page.get_by_label('Password').fill('wrong')
                    page.get_by_role('button', name='Sign In').click()
                    expect(page.get_by_role('alert')).to_contain_text('Invalid email or password')
                    page.get_by_label('Password').fill('synthetic-password')
                    page.get_by_role('button', name='Sign In').click()
                    expect(page.get_by_role('alert')).to_contain_text('does not match the selected sign-in role')
                    assert page.evaluate("sessionStorage.getItem('access_token')") is None
                    expect(page.get_by_role('heading', name='EIC Dashboard')).to_have_count(0)
                    # A selected EIC role did not turn the server's author into an EIC.
                    page.get_by_role('button', name='Choose a different role').click()
                    page.get_by_role('button', name='Author / Submitter', exact=True).click()
                    page.get_by_role('button', name='Register', exact=True).click()
                    expect(page.get_by_text('Registration creates an Author / Submitter account.')).to_be_visible()
                    page.get_by_label('Full Name').fill('Synthetic User')
                    page.get_by_label('Email Address').fill('test@example.invalid')
                    page.get_by_label('Password').fill('synthetic-password')
                    page.get_by_role('button', name='Create Account').click()
                    expect(page.get_by_role('status')).to_contain_text('Registration successful')
                    page.get_by_label('Password').fill('synthetic-password')
                    page.get_by_role('button', name='Sign In').click()
                    expect(page.get_by_text('Author Dashboard · Synthetic User')).to_be_visible()
                    page.reload()
                    expect(page.get_by_text('Author Dashboard · Synthetic User')).to_be_visible()
                    page.get_by_role('button', name='Logout').click()
                    expect(page.get_by_role('heading', name='G-Sub.')).to_be_visible()
                    assert page.evaluate("sessionStorage.getItem('access_token')") is None
                    role['value'] = 'formatter'
                    page.get_by_role('button', name='Formatter sign in').click()
                    expect(page.get_by_text('Selected role: Formatter')).to_be_visible()
                    page.get_by_label('Email Address').fill('test@example.invalid')
                    page.get_by_label('Password').fill('synthetic-password')
                    page.get_by_role('button', name='Sign In').click()
                    expect(page.get_by_role('heading', name='Formatter Dashboard')).to_be_visible()
                    page.get_by_role('button', name='Logout').click()
                    for role_id, label, dashboard in [
                        ('eic', 'Editor-in-Chief', 'EIC Dashboard'),
                        ('editor', 'Editor', 'Editor Dashboard · Synthetic User'),
                        ('reviewer', 'Peer Reviewer', 'Reviewer Dashboard · Synthetic User'),
                    ]:
                        role['value'] = role_id
                        page.get_by_role('button', name=label, exact=True).click()
                        page.get_by_label('Email Address').fill('test@example.invalid')
                        page.get_by_label('Password').fill('synthetic-password')
                        page.get_by_role('button', name='Sign In').click()
                        expect(page.get_by_text(dashboard, exact=True)).to_be_visible()
                        page.get_by_role('button', name='Logout').click()
                    page.set_viewport_size({'width': 375, 'height': 812})
                    expect(page.get_by_role('button', name='Peer Reviewer', exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    boxes = [page.get_by_role('button', name=label, exact=True).bounding_box() for label in roles]
                    assert all(boxes[i]['y'] < boxes[i + 1]['y'] for i in range(3))
                    print('PASS: role cards, keyboard focus, login errors, mismatch, registration, author session restore/logout, formatter and mobile layout')
                    print('PASS: login and registration payloads contain no selected role; all API traffic intercepted')
                    browser.close()
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
