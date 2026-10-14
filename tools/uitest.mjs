// Offline first-run browser checks always own a fresh, disposable account.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import assert from 'node:assert/strict';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const home = mkdtempSync(join(tmpdir(), 'nightdesk-ui-test-'));
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const localPython = join(root, '.venv', 'bin', 'python');
const python = process.env.NIGHTDESK_TEST_PYTHON || (existsSync(localPython) ? localPython : 'python3');
const engine = spawn(python, ['-u', '-m', 'nightdesk'], {
  cwd: root, env: {...process.env, PYTHONPATH:join(root,'engine'), NIGHTDESK_HOME:home,
    NIGHTDESK_PORT:String(port), NIGHTDESK_READER_PYTHON:'', NIGHTDESK_READER_MODEL:'',
    PYTHONDONTWRITEBYTECODE:'1'}, stdio:['ignore','pipe','pipe']
});
let logs = '', browser;
engine.stdout.on('data', chunk => { logs += chunk; });
engine.stderr.on('data', chunk => { logs += chunk; });
const api = async (path, body) => fetch(origin + path, body === undefined ? {} : {
  method:'POST',headers:{'Content-Type':'application/json','X-Nightdesk':'1'},body:JSON.stringify(body)});
try {
  let ready = false;
  for (let i=0;i<150;i++) {
    try { if ((await (await api('/api/health')).json()).app === 'nightdesk') { ready=true;break; } } catch {}
    if (engine.exitCode !== null) throw new Error('Test engine failed: '+logs);
    await new Promise(resolve => setTimeout(resolve,200));
  }
  assert(ready,'Test engine did not start');
  const setup = await (await api('/api/setup')).json();
  const settings = await (await api('/api/settings')).json();
  assert.equal(setup.network_enabled,false);
  assert.equal(setup.ready,false);
  assert.equal(settings.settings.auto_trade,false);
  assert.equal((await api('/api/setup',{network_enabled:'true',start:true})).status,400);
  assert.equal((await (await api('/api/control',{action:'resume'})).json()).ok,false);
  assert.equal((await api('/api/settings',{auto_trade:true})).status,409);
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1320,height:860}});
  const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
    const url=route.request().url();
    if (url.startsWith(origin+'/')) return route.continue();
    external.push(url);return route.abort();
  });
  await page.addInitScript(()=>localStorage.setItem('nd.guide.done','1'));
  await page.goto(origin);
  await page.waitForSelector('[data-act="setup"]');
  assert.match(await page.locator('#view').innerText(),/starts offline/i);
  for (const view of ['portfolio','results','activity','settings','help','home']) {
    await page.locator(`#nav [data-nav="${view}"]`).click();
    await page.waitForTimeout(250);
    assert(await page.locator('#view').innerText(),view+' is empty');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,view+' overflows');
  }
  await page.setViewportSize({width:760,height:700});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,'small window overflows');
  assert.deepEqual(errors,[]);
  assert.deepEqual(external,[]);
  const final = await (await api('/api/setup')).json();
  assert.equal(final.network_enabled,false);
  assert.equal(final.ready,false);
  console.log('PASS: offline first launch, consent guards, paused practice account, 6 screens, 2 sizes, no page errors or external browser requests.');
} finally {
  if (browser) await browser.close();
  engine.kill('SIGTERM');
  if (engine.exitCode === null) await Promise.race([
    new Promise(resolve=>engine.once('exit',resolve)),
    new Promise(resolve=>setTimeout(()=>{engine.kill('SIGKILL');resolve();},5000))
  ]);
  rmSync(home,{recursive:true,force:true});
}
