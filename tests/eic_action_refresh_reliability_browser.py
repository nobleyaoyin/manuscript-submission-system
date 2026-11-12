"""EIC action outcomes against isolated backend and synthetic fixtures."""
import json
import os
import signal
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from author_agreement_panel_sync_browser import free_port, prepare_pending, wait_ready

PROJECT = Path(__file__).resolve().parents[1]


def seed_other_workflows(root, fixture, pending):
    """Add only disposable formatter/final-edit fixtures; never exercise EIC Accept."""
    with sqlite3.connect(root / 'browser.sqlite') as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute("UPDATE manuscripts SET status='revised' WHERE manuscript_id=?", (pending['docx']['manuscript_id'],))
        mid=pending['legacy']['manuscript_id']; author=fixture['user_ids']['author']; eic=fixture['user_ids']['eic']; editor=fixture['user_ids']['editor']
        db.execute("UPDATE manuscripts SET status='final_editing' WHERE manuscript_id=?", (mid,))
        db.execute("UPDATE author_agreements SET status='accepted',responded_at=datetime('now') WHERE agreement_id=?", (pending['legacy']['agreement_id'],))
        db.execute("INSERT INTO users (name,email,password_hash,role,created_at) SELECT 'Synthetic Formatter','formatter@example.com',password_hash,'formatter',datetime('now') FROM users WHERE user_id=?", (editor,))
        iid=db.execute("INSERT INTO final_editor_invitations (manuscript_id,editor_id,invited_by,status,invited_at,responded_at) VALUES (?,?,?,'accepted',datetime('now'),datetime('now'))", (mid,editor,eic)).lastrowid
        sid=db.execute("INSERT INTO final_editing_submissions (manuscript_id,editor_id,file_path,comments,status,author_reviewed_at,eic_reviewed_at,eic_approved_by,created_at) VALUES (?,?,?,'Synthetic','author_approved',datetime('now'),datetime('now'),?,datetime('now'))", (mid,editor,f'uploads/final_editing/{mid}/synthetic.pdf',eic)).lastrowid
        db.execute('INSERT INTO final_editing_submission_invitations (submission_id,invitation_id) VALUES (?,?)',(sid,iid))
        db.commit()
    path=root/'uploads'/'final_editing'/str(mid)/'synthetic.pdf';path.parent.mkdir(parents=True);path.write_bytes(b'%PDF-synthetic-final')


def main():
    with tempfile.TemporaryDirectory(prefix='reviewer-e2e-') as temporary:
        root=Path(temporary).resolve(); backend,frontend=free_port(),free_port()
        if backend==frontend: raise RuntimeError('Port collision')
        base,origin=f'http://127.0.0.1:{backend}',f'http://127.0.0.1:{frontend}'
        env=os.environ.copy();env.update(REVIEWER_E2E_ROOT=str(root),REVIEWER_E2E_BACKEND_PORT=str(backend),REVIEWER_E2E_ORIGIN=origin,PYTHONPATH=str(PROJECT),VITE_API_PROXY_TARGET=base)
        processes=[]
        with (root/'server.log').open('w+') as log:
            try:
                server=subprocess.Popen([str(PROJECT/'.venv/bin/python'),str(PROJECT/'tests/reviewer_browser_server.py')],cwd=PROJECT,env=env,stdout=log,stderr=log,start_new_session=True);processes.append(server);wait_ready(server,base+'/health')
                fixture=json.loads((root/'fixtures.json').read_text());pending=prepare_pending(root,fixture);seed_other_workflows(root,fixture,pending)
                vite=subprocess.Popen(['npm','run','dev','--','--host','127.0.0.1','--port',str(frontend),'--strictPort'],cwd=PROJECT/'frontend',env=env,stdout=log,stderr=log,start_new_session=True);processes.append(vite);wait_ready(vite,origin)
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True);context=browser.new_context();page=context.new_page();api=context.request
                    def headers(role):return {'Authorization':'Bearer '+fixture['tokens'][role]}
                    for label in ('round1','pdf','doc'):
                        response=api.post(base+f"/agreements/{pending[label]['agreement_id']}/respond",headers=headers('author'),data={'decision':'accept','agreement_version':'placeholder-v1'})
                        assert response.status==200,response.status
                    page.goto(origin);page.locator('#email').fill('eic@example.com');page.locator('#password').fill(fixture['password']);page.get_by_role('button',name='Sign In').click();page.get_by_role('heading',name='EIC Dashboard').wait_for()
                    main=page.locator('main')
                    def choose(label):page.get_by_role('button',name=f"#{pending[label]['manuscript_id']} Fixture {label}").click()
                    def final_form():return main.locator('form').filter(has_text='Invite final editor')
                    def fill_editor():final_form().get_by_label('Editor').select_option(label='Editor')
                    def action_error():return page.locator('.eic-error')

                    choose('round1');fill_editor();final_form().get_by_role('button',name='Send final editor invitation').click()
                    expect(page.get_by_role('status').filter(has_text='Action completed.')).to_be_visible()
                    main.get_by_text('Editor — pending').wait_for()
                    assert action_error().count()==0
                    duplicate=api.post(base+'/final-workflow/invite',headers=headers('eic'),data={'manuscript_id':pending['round1']['manuscript_id'],'editor_id':fixture['user_ids']['editor']})
                    assert duplicate.status==409,duplicate.status
                    print('PASS EIC action and refresh; real backend duplicate invitation rejected')

                    choose('pdf');fill_editor();mid=pending['pdf']['manuscript_id']
                    page.route(f'**/api/eic/manuscripts/{mid}/overview',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic refresh failure"}'))
                    final_form().get_by_role('button',name='Send final editor invitation').click()
                    expect(page.get_by_role('status').filter(has_text='Action completed.')).to_be_visible()
                    expect(action_error()).to_contain_text('Action saved, but dashboard refresh failed')
                    expect(action_error()).to_contain_text('do not repeat the action')
                    expect(final_form().get_by_role('button',name='Send final editor invitation')).to_be_disabled()
                    choose('round1');expect(action_error()).to_contain_text('Action saved, but dashboard refresh failed')
                    page.unroute(f'**/api/eic/manuscripts/{mid}/overview');choose('pdf')
                    expect(action_error()).to_contain_text('Action saved, but dashboard refresh failed')
                    page.get_by_role('button',name='Refresh dashboard').click()
                    main.get_by_text('Editor — pending').wait_for();expect(action_error()).to_have_count(0)
                    print('PASS EIC saved action and failed read separated; duplicate UI blocked until refresh')

                    choose('doc');fill_editor()
                    page.route('**/api/final-workflow/invite',lambda route:route.fulfill(status=409,content_type='application/json',body='{"detail":"Synthetic rejection"}'))
                    final_form().get_by_role('button',name='Send final editor invitation').click()
                    expect(action_error()).to_contain_text('Action request rejected: Synthetic rejection')
                    assert page.get_by_role('status').filter(has_text='Action completed.').count()==0
                    expect(final_form().get_by_role('button',name='Send final editor invitation')).to_be_enabled()
                    page.unroute('**/api/final-workflow/invite')
                    page.route('**/api/final-workflow/invite',lambda route:route.abort('failed'))
                    final_form().get_by_role('button',name='Send final editor invitation').click()
                    expect(action_error()).to_contain_text('outcome could not be confirmed and may have been saved')
                    expect(final_form().get_by_role('button',name='Send final editor invitation')).to_be_disabled()
                    page.unroute('**/api/final-workflow/invite');page.get_by_role('button',name='Refresh dashboard').click();expect(action_error()).to_have_count(0)
                    expect(final_form().get_by_role('button',name='Send final editor invitation')).to_be_enabled()
                    print('PASS confirmed 409 rejection and ambiguous network failure distinguished')
                    page.route('**/api/final-workflow/invite',lambda route:route.fulfill(status=500,content_type='application/json',body='{}'))
                    final_form().get_by_role('button',name='Send final editor invitation').click()
                    expect(action_error()).to_contain_text('outcome could not be confirmed and may have been saved')
                    expect(final_form().get_by_role('button',name='Send final editor invitation')).to_be_disabled()
                    page.unroute('**/api/final-workflow/invite');page.get_by_role('button',name='Refresh dashboard').click();expect(action_error()).to_have_count(0)
                    print('PASS HTTP 500 is treated as an uncertain write outcome')

                    choose('docx');mid=pending['docx']['manuscript_id'];reviewers=main.get_by_role('region',name='Reviewer invitations')
                    reviewers.get_by_label('Reviewer ID').fill(str(fixture['user_ids']['other']))
                    page.route(f'**/api/assignments/manuscript/{mid}',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
                    reviewers.get_by_role('button',name='Invite reviewer',exact=True).click()
                    expect(reviewers.get_by_role('status')).to_contain_text('Reviewer invitation sent.')
                    expect(reviewers.get_by_role('alert')).to_contain_text('Invitation saved, but refresh failed')
                    expect(reviewers.get_by_role('button',name='Invite reviewer',exact=True)).to_be_disabled()
                    duplicate=api.post(base+'/assignments/',headers=headers('eic'),data={'manuscript_id':mid,'reviewer_id':fixture['user_ids']['other'],'review_round':2});assert duplicate.status==409
                    page.unroute(f'**/api/assignments/manuscript/{mid}');reviewers.get_by_role('button',name='Refresh reviewer invitations').click();expect(reviewers.get_by_role('alert')).to_have_count(0)
                    print('PASS reviewer invitation saved with failed refresh and real duplicate rejected')

                    choose('legacy');mid=pending['legacy']['manuscript_id'];formatting=main.get_by_role('region',name='Formatting workflow')
                    form=formatting.locator('form').filter(has_text='Invite formatter');form.get_by_label('Formatter').select_option(label='Synthetic Formatter')
                    page.route(f'**/api/formatting/manuscripts/{mid}',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
                    form.get_by_role('button',name='Send formatter invitation').click()
                    expect(formatting.get_by_role('status')).to_contain_text('Action completed.')
                    expect(formatting.get_by_role('alert')).to_contain_text('Action saved, but refresh failed')
                    expect(form.get_by_role('button',name='Send formatter invitation')).to_be_disabled()
                    formatters=api.get(base+'/formatting/formatters',headers=headers('eic')).json()
                    formatter_id=next(item['user_id'] for item in formatters if item['name']=='Synthetic Formatter')
                    duplicate=api.post(base+'/formatting/invite',headers=headers('eic'),data={'manuscript_id':mid,'formatter_id':formatter_id});assert duplicate.status==409
                    page.unroute(f'**/api/formatting/manuscripts/{mid}');formatting.get_by_role('button',name='Refresh formatting').click()
                    formatting.get_by_text('Synthetic Formatter — pending').wait_for();expect(formatting.get_by_role('alert')).to_have_count(0)
                    print('PASS formatter invitation saved with failed refresh; explicit refresh recovers')
                    browser.close()
            finally:
                for process in reversed(processes):
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                for process in reversed(processes):
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()


if __name__=='__main__':main()
