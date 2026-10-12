/* Real Chromium media clock, speed and seek checks; synthetic audio only. */
const { chromium } = require('../frontend/node_modules/playwright-core');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');

async function main() {
  const port = await new Promise(resolve => {
    const socket = net.createServer();
    socket.listen(0, '127.0.0.1', () => {const value = socket.address().port; socket.close(() => resolve(value));});
  });
  const directory = path.join(root, '.cache', 'audio-browser');
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const config = path.join(directory, 'vite.config.mjs');
  fs.writeFileSync(config, `import react from '${root}/frontend/node_modules/@vitejs/plugin-react/dist/index.js';
export default {plugins:[react()], server:{hmr:false}};`);
  const log = fs.openSync(path.join(directory, 'vite.log'), 'w', 0o600);
  const vite = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--config', config, '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {cwd: path.join(root, 'frontend'), env: {...process.env, API_PROXY_TARGET: 'http://127.0.0.1:1'}, stdio: ['ignore', log, log]});
  let browser;
  let page;
  const errors = [];
  let stage = "startup";
  try {
    const url = `http://127.0.0.1:${port}`;
    for (let attempt = 0; ; attempt++) {
      try {if ((await fetch(`${url}/src/main.tsx`)).ok) break;} catch {}
      if (attempt >= 40) throw new Error('Validation Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const executablePath = process.env.KNIGOSKOP_CHROMIUM_PATH || '/usr/bin/chromium';
    browser = await chromium.launch({executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking']});
    page = await browser.newPage();
    page.on("console", m => {if (m.type() === "error") errors.push(m.text());});
    page.on("requestfailed", r => errors.push(r.url() + " " + r.failure()?.errorText));
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', route => new URL(route.request().url()).pathname.startsWith('/api/v1/') ? route.fulfill({contentType: 'application/json', body: JSON.stringify({revision: 1, position_ms: 0, playback_rate: 1})}) : route.continue());
    const audio = Buffer.alloc(44 + 8000 * 2 * 2);
    audio.write('RIFF'); audio.writeUInt32LE(audio.length - 8, 4); audio.write('WAVEfmt ', 8);
    audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22);
    audio.writeUInt32LE(8000, 24); audio.writeUInt32LE(16000, 28); audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34);
    audio.write('data', 36); audio.writeUInt32LE(audio.length - 44, 40);
    await page.route('**/@vite/client', route => route.fulfill({contentType: 'application/javascript', body: 'export function createHotContext(){return undefined;} export function injectQuery(url){return url;} export function updateStyle(){} export function removeStyle(){}'}));
    await page.route('**/fixture.wav', route => {
      const range = route.request().headers()['range'];
      const match = range?.match(/^bytes=(\d+)-(\d*)$/);
      const start = match ? Number(match[1]) : 0;
      const end = match?.[2] ? Math.min(Number(match[2]), audio.length - 1) : audio.length - 1;
      return route.fulfill({status: match ? 206 : 200, contentType: 'audio/wav', headers: {'Accept-Ranges': 'bytes', ...(match ? {'Content-Range': `bytes ${start}-${end}/${audio.length}`} : {})}, body: audio.subarray(start, end + 1)});
    });
    const manifest = {id: 'fixture', text: 'Первое Второе Третье', voice: 'demo', duration_ms: 2000, demo: true, progress: {position_ms: 0, playback_rate: 1, revision: 0}, segments: [{index: 0, start_ms: 0, duration_ms: 2000, text_start: 0, text_end: 20, alignment: 'words', url: '/fixture.wav', timings: [{start_ms: 0, end_ms: 700, text_start: 0, text_end: 6}, {start_ms: 700, end_ms: 1300, text_start: 7, text_end: 13}, {start_ms: 1300, end_ms: 2000, text_start: 14, text_end: 20}]}]};
    await page.route('**/audio-fixture', route => route.fulfill({contentType: 'text/html', body: `<html lang="ru"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module">
      import React from '/node_modules/.vite/deps/react.js';
      import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {};
      window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      const {MediaPlayer} = await import('/src/features/audio/AudioPanel.tsx');
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MediaPlayer,{manifest:${JSON.stringify(manifest)}}));
      </script></body></html>`}));
    await page.goto(`${url}/audio-fixture`);
    await page.getByRole('button', {name: 'Воспроизвести', exact: true}).click();
    stage = 'native_clock_1x';
    await page.waitForFunction(() => document.querySelector('audio').currentTime > 0.15);
    const clockAt1 = await page.locator('audio').evaluate(audio => ({rate: audio.playbackRate, time: audio.currentTime}));
    assert.equal(clockAt1.rate, 1);
    await page.getByLabel('Скорость', {exact: true}).selectOption('1.5');
    assert.equal(await page.locator('audio').evaluate(audio => audio.playbackRate), 1.5);
    await page.getByRole('button', {name: 'Пауза', exact: true}).click();
    await page.getByRole('button', {name: 'Второе', exact: true}).click();
    stage = 'seek_second';
    await page.waitForFunction(() => document.querySelector('[aria-current="true"]')?.textContent.trim() === 'Второе');
    assert.ok(Math.abs((await page.locator('audio').evaluate(audio => audio.currentTime)) - 0.7) < 0.1);
    await page.getByRole('button', {name: 'Воспроизвести', exact: true}).click();
    stage = 'native_clock_1_5x';
    await page.waitForFunction(() => document.querySelector('[aria-current="true"]')?.textContent.trim() === 'Третье');
    await page.getByRole('button', {name: 'Пауза', exact: true}).click();
    await page.getByRole('button', {name: 'Первое', exact: true}).click();
    stage = 'seek_first';
    await page.waitForFunction(() => document.querySelector('[aria-current="true"]')?.textContent.trim() === 'Первое');
    assert.deepEqual(errors, []);
    const report = {passed: true, real_chromium: true, real_wav_decode: true, speeds: [1, 1.5], native_media_clock: true, forward_and_backward_phrase_seek: true, paid_calls: false, browser_version: browser.version()};
    fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    if (page) console.error(JSON.stringify({stage, media: await page.locator('audio').evaluate(a => ({time: a.currentTime, rate: a.playbackRate, paused: a.paused, ended: a.ended, duration: a.duration, seekable: Array.from({length:a.seekable.length}, (_,i)=>[a.seekable.start(i),a.seekable.end(i)]), buffered: Array.from({length:a.buffered.length}, (_,i)=>[a.buffered.start(i),a.buffered.end(i)]), error: a.error?.code})).catch(() => null), active: await page.locator('[aria-current="true"]').allTextContents()}));
    console.error(JSON.stringify(errors).replace(/token=[A-Za-z0-9_-]+/g, 'token=[redacted]'));
    if (page) fs.writeFileSync(path.join(directory, "failed-page.html"), await page.content());
    throw error;
  } finally {
    if (browser) await browser.close();
    vite.kill('SIGTERM');
    await new Promise(resolve => vite.once('exit', resolve));
    fs.closeSync(log);
  }
}
main().catch(error => {console.error(error.message); process.exitCode = 1;});
