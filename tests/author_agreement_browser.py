"""Disposable browser test for a declined prototype agreement."""
import json,os,signal,socket,subprocess,tempfile,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def wait_ready(process,url):
    for _ in range(150):
        if process.poll() is not None:raise RuntimeError('Server exited')
        try:
            with urllib.request.urlopen(url,timeout=1):return
        except OSError:time.sleep(.1)
    raise RuntimeError('Server startup timeout')

def main():
    with tempfile.TemporaryDirectory(prefix='reviewer-e2e-') as temporary:
        root=Path(temporary).resolve();backend,frontend=free_port(),free_port()
        if backend==frontend:raise RuntimeError('Port collision')
        base=f'http://127.0.0.1:{backend}';origin=f'http://127.0.0.1:{frontend}'
        env=os.environ.copy();env.update(REVIEWER_E2E_ROOT=str(root),REVIEWER_E2E_BACKEND_PORT=str(backend),REVIEWER_E2E_ORIGIN=origin,PYTHONPATH=str(PROJECT),VITE_API_PROXY_TARGET=base)
        processes=[]
        with (root/'server.log').open('w+') as log:
            try:
                server=subprocess.Popen([str(PROJECT/'.venv/bin/python'),str(PROJECT/'tests/reviewer_browser_server.py')],cwd=PROJECT,env=env,stdout=log,stderr=log,start_new_session=True);processes.append(server);wait_ready(server,base+'/health')
                vite=subprocess.Popen(['npm','run','dev','--','--host','127.0.0.1','--port',str(frontend),'--strictPort'],cwd=PROJECT/'frontend',env=env,stdout=log,stderr=log,start_new_session=True);processes.append(vite);wait_ready(vite,origin)
                fixture=json.loads((root/'fixtures.json').read_text());info=fixture['assignments']['round1'];mid=info['manuscript_id']
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True);context=browser.new_context();api=context.request
                    def headers(role):return {'Authorization':'Bearer '+fixture['tokens'][role]}
                    review=api.post(base+'/reviews/',headers=headers('reviewer'),data={'assignment_id':info['id'],'comments':'Synthetic review','recommendation':'accept'});assert review.status==201,(review.status,review.text())
                    decision=api.post(base+f'/eic/manuscripts/{mid}/decision',headers=headers('eic'),data={'decision':'accept','comments':'Accepted'});assert decision.status==201,(decision.status,decision.text())
                    page=context.new_page();page.goto(origin);page.locator('#email').fill('author@example.com');page.locator('#password').fill(fixture['password']);page.get_by_role('button',name='Sign In').click()
                    panel=page.get_by_role('region',name='Author agreements');panel.get_by_text('Prototype placeholder:',exact=False).wait_for()
                    card=panel.locator('article').filter(has_text='Fixture round1');card.get_by_role('button',name='Decline prototype agreement').click();card.get_by_text('Status: declined',exact=False).wait_for();card.get_by_text('Final editing is on hold',exact=False).wait_for()
                    agreements=api.get(base+'/agreements/my',headers=headers('author')).json();item=next(a for a in agreements if a['manuscript_id']==mid)
                    repeated=api.post(base+f"/agreements/{item['agreement_id']}/respond",headers=headers('author'),data={'decision':'decline','agreement_version':item['agreement_version']});assert repeated.status==409
                    upload=api.post(base+f'/eic/final-editing/{mid}/submit',headers=headers('editor'),multipart={'comments':'edit','file':{'name':'final.pdf','mimeType':'application/pdf','buffer':b'%PDF-final'}});assert upload.status==409
                    overview=api.get(base+f'/eic/manuscripts/{mid}/overview',headers=headers('eic'));assert overview.status==200 and overview.json()['author_agreement']['status']=='declined'
                    dashboard=api.get(base+'/manuscripts/dashboard',headers=headers('editor'));assert next(m for m in dashboard.json() if m['manuscript_id']==mid)['author_agreement_status']=='declined'
                    browser.close();print('PASS author browser decline, duplicate rejection, final-editing hold, EIC/editor visibility')
            finally:
                for process in reversed(processes):
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                for process in reversed(processes):
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()

if __name__=='__main__':main()
