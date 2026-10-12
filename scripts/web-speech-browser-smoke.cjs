/* Native Web Speech API through the actual component; no mocked speech engine. */
const { chromium } = require('../frontend/node_modules/playwright-core');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');

async function main() {
  const port = await new Promise(resolve => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
  const directory = path.join(root, '.cache', 'web-speech-browser');
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const config = path.join(directory, 'vite.config.mjs');
  fs.writeFileSync(config, `import react from '${root}/frontend/node_modules/@vitejs/plugin-react/dist/index.js'; export default {plugins:[react()], server:{hmr:false}};`);
  const log = fs.openSync(path.join(directory, 'vite.log'), 'w', 0o600);
  const vite = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--config', config, '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: path.join(root, 'frontend'), env: { ...process.env, API_PROXY_TARGET: 'http://127.0.0.1:1' }, stdio: ['ignore', log, log]
  });
  let browser;
  try {
    const url = `http://127.0.0.1:${port}`;
    for (let attempt = 0; ; attempt++) {
      try { if ((await fetch(`${url}/src/main.tsx`)).ok) break; } catch {}
      if (attempt >= 50) throw new Error('Validation Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({ executablePath: process.env.KNIGOSKOP_CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/@vite/client', route => route.fulfill({contentType:'application/javascript', body:'export function createHotContext(){return undefined;} export function injectQuery(url){return url;} export function updateStyle(){} export function removeStyle(){}'}));
    await page.route('**/web-speech-fixture', route => route.fulfill({contentType:'text/html',body:`<html lang="ru"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module">
      import React from '/node_modules/.vite/deps/react.js';
      import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
      window.__vite_plugin_react_preamble_installed__=true;
      const {BrowserSpeech}=await import('/src/features/audio/AudioPanel.tsx');
      window.speechEvents=[];
      function Fixture(){
        const [consent,setConsent]=React.useState(false);
        return React.createElement(React.Fragment,null,
          React.createElement('label',null,'Согласие',React.createElement('input',{type:'checkbox',checked:consent,onChange:e=>setConsent(e.target.checked)})),
          React.createElement(BrowserSpeech,{text:'Книгоскоп. Проверка голоса браузера.',consent,complete:true}));
      }
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Fixture));
      </script></body></html>`}));
    await page.goto(`${url}/web-speech-fixture`);
    const play = page.getByRole('button', { name: 'Озвучить в браузере', exact: true });
    await play.waitFor();
    assert.equal(await play.isDisabled(), true);
    await page.getByLabel('Согласие', { exact: true }).check();
    assert.equal(await play.isEnabled(), true);
    const native = await page.evaluate(() => ({ supported: typeof speechSynthesis !== 'undefined', voices: speechSynthesis.getVoices().map(v => ({name:v.name,lang:v.lang,localService:v.localService})) }));
    // Observe native events on real utterances without replacing speak or synthesis.
    await page.evaluate(() => {
      const nativeConstructor = window.SpeechSynthesisUtterance;
      window.SpeechSynthesisUtterance = class extends nativeConstructor {
        constructor(text) {
          super(text);
          for (const type of ['start','end','error','boundary','pause','resume'])
            this.addEventListener(type, event => window.speechEvents.push({type,error:event.error || null,charIndex:event.charIndex}));
        }
      };
    });
    await page.getByLabel('Скорость голоса браузера', { exact: true }).selectOption('1.5');
    await play.click();
    await page.waitForFunction(() => window.speechEvents.some(e => e.type === 'end' || e.type === 'error'), null, {timeout:15000});
    const events = await page.evaluate(() => window.speechEvents);
    const speechSucceeded = events.some(e => e.type === 'start') && events.some(e => e.type === 'end') && !events.some(e => e.type === 'error');
    if (!speechSucceeded) {
      assert.ok(events.some(e => e.type === 'error'));
      await page.getByRole('alert').filter({hasText:'Голос браузера недоступен.'}).waitFor();
      assert.equal(await page.locator('.karaoke-active').count(), 0);
      assert.equal(await page.getByRole('button', {name:'Пауза голоса браузера',exact:true}).isDisabled(), true);
    }
    assert.deepEqual(errors, []);
    const report = { component_checks_passed:true, real_chromium:true, native_speech_api:true, speech_engine_mocked:false, consent_required:true, ...native, events, speech_succeeded:speechSucceeded, audible_output_verified:false, pause_resume_with_real_voice_verified:false, paid_provider_calls:false, browser_version:browser.version() };
    fs.writeFileSync(path.join(directory,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {
    if (browser) await browser.close();
    const exit = new Promise(resolve => vite.once('exit',resolve));
    vite.kill('SIGTERM');
    await exit;
    fs.closeSync(log);
  }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
