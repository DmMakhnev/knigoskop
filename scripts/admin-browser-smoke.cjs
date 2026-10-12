/* Actual administrative UI in Chromium; synthetic intercepted API responses. */
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
  const directory = path.join(root, '.cache', 'admin-browser');
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

    let role = 'admin';
    const calls = [];
    const errors = [];
    const provider = {id:'provider',name:'Fixture provider',protocol:'openai_chat',base_url:'https://api.example.com/v1',host:'api.example.com',enabled:true,has_key:true,key_mask:'••••••••'};
    const model = {id:'model',provider_id:'provider',model_identifier:'fixture-model',enabled:true,capabilities:{max_context_tokens:4096,mock_behavior:'success'},pricing:{}};
    const stats = {users_total:2,jobs:{completed:1},llm:{calls:1,input_tokens:10,output_tokens:5,mean_latency_ms:100},tts:{calls:0,chars:0,duration_ms:0},costs:[{currency:'RUB',estimated_cost_micros:1000000,unknown_cost_calls:1},{currency:'USD',estimated_cost_micros:2000000,unknown_cost_calls:0}],models:[],tts_profiles:[]};
    await browser.newContext().then(async context => {
      await context.addCookies([{name:'knigoskop_csrf',value:'x'.repeat(43),url}]);
      await context.route('**/api/**', async route => {
        const req = route.request(); const parsed = new URL(req.url());
        if (!parsed.pathname.startsWith('/api/v1/')) return route.continue();
        const endpoint = parsed.pathname.slice('/api/v1'.length);
        calls.push({endpoint,method:req.method()});
        if(endpoint==='/me') return route.fulfill({contentType:'application/json',body:JSON.stringify({id:'actor',email:'actor@example.com',role,status:'active',verified_at:'2026-10-13'})});
        if(endpoint==='/auth/csrf') return route.fulfill({contentType:'application/json',body:'{}'});
        if(endpoint==='/admin/providers' && req.method()==='POST'){
          const payload=req.postDataJSON();
          assert.equal(payload.api_key,'fixture-secret-key');
          assert.equal(req.headers()['x-csrf-token'],'x'.repeat(43));
          return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({...provider,id:'new-provider',name:payload.name})});
        }
        const responses = {
          '/admin/users':{items:[{id:'reader',email:'reader@example.com',role:'user',status:'active'}],total:1},
          '/admin/providers':{items:[provider],total:1},
          '/admin/models':{items:[model],total:1},
          '/admin/routes':{items:[],total:0},
          '/admin/ai-status':{master_key_configured:true,route_configured:false},
          '/admin/statistics':stats,
          '/admin/job-monitor':{items:[{id:'job',user_id:'reader',type:'processing',status:'failed',progress:0,attempts:1,error_code:'provider_unavailable',created_at:'2026-10-13'}],total:1},
          '/admin/audit':{items:[{id:'event',actor_id:'actor',action:'provider_created',entity_id:'provider',created_at:'2026-10-13'}],total:1},
          '/admin/quotas':{defaults:{books:100,storage_bytes:536870912},overrides:{items:[],total:0}},
          '/admin/sources':{items:[],total:0},
          '/admin/audio/profiles':{items:[]},
          '/admin/audio/routes':{items:[]},
          '/admin/prompts':{items:[]},
          '/admin/usage':{items:[],total:0},
          '/admin/audio/usage':{items:[],total:0},
        };
        if(!(endpoint in responses)) return route.fulfill({status:404,contentType:'application/json',body:'{"message":"Fixture endpoint not defined"}'});
        return route.fulfill({contentType:'application/json',body:JSON.stringify(responses[endpoint])});
      });
      await context.route('**/@vite/client', route=>route.fulfill({contentType:'application/javascript',body:`export function createHotContext(){return {accept(){},prune(){},dispose(){},on(){},send(){},invalidate(){},data:{}};} export function injectQuery(url){return url;} const styles=new Map(); export function updateStyle(id,css){let style=styles.get(id);if(!style){style=document.createElement('style');document.head.appendChild(style);styles.set(id,style);}style.textContent=css;} export function removeStyle(id){styles.get(id)?.remove();styles.delete(id);}`}));
      const entry = path.join(root,'frontend/.cache/admin-browser-fixture.tsx');
      fs.mkdirSync(path.dirname(entry),{recursive:true,mode:0o700});
      fs.writeFileSync(entry,`import React from 'react';
        import {createRoot} from 'react-dom/client';
        import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
        import {MemoryRouter} from 'react-router-dom';
        import {App} from '../src/app/App';
        import '../src/styles/main.css';
        createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}})}><MemoryRouter initialEntries={['/admin']}><App/></MemoryRouter></QueryClientProvider>);`);
      await context.route('**/admin-fixture',route=>route.fulfill({contentType:'text/html',body:`<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
        await import('/.cache/admin-browser-fixture.tsx');
        </script></body></html>`}));
      const page = await context.newPage(); page.on('pageerror',e=>{errors.push(e.message);console.error(e.message);});
      await page.goto(`${url}/admin-fixture`);
      await page.getByRole('heading',{name:'Пользователи',exact:true}).waitFor().catch(async e=>{console.error(await page.locator('body').innerText());console.error(JSON.stringify(errors));throw e;});
      await page.getByRole('button',{name:'Провайдеры',exact:true}).click();
      await page.getByLabel('Название провайдера',{exact:true}).fill('New provider');
      await page.getByLabel('URL API',{exact:true}).fill('https://api.example.com/v1');
      await page.getByLabel('Новый API-ключ',{exact:true}).fill('fixture-secret-key');
      await page.getByRole('button',{name:'Сохранить провайдера',exact:true}).click();
      await page.getByText('Настройки сохранены.',{exact:true}).waitFor();
      assert.equal(await page.getByLabel('Новый API-ключ',{exact:true}).inputValue(),'');
      assert.equal(await page.getByText('fixture-secret-key',{exact:true}).count(),0);
      const statsButton=page.getByRole('button',{name:'Статистика',exact:true});
      await statsButton.focus();await page.keyboard.press('Enter');
      await page.getByText('RUB',{exact:true}).waitFor();
      assert.ok(await page.getByText('USD',{exact:true}).isVisible());
      assert.ok(await page.getByText('1.000000',{exact:true}).isVisible());
      await page.getByRole('button',{name:'Задачи',exact:true}).click();
      await page.getByText('Код ошибки: provider_unavailable',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Аудит',exact:true}).click();
      await page.getByText(/provider_created/).waitFor();
      for(const section of ['Модели','Маршруты','Промпты','Озвучивание','Лимиты','Источники','Запросы','Диагностика']){
        await page.getByRole('button',{name:section,exact:true}).click();
        await page.waitForTimeout(100);
      }
      await page.setViewportSize({width:390,height:844});
      await page.getByRole('button',{name:'Статистика',exact:true}).click();
      await page.getByText('RUB',{exact:true}).waitFor();
      const overflow=await page.evaluate(()=>({width:window.innerWidth,scroll:document.documentElement.scrollWidth,items:Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>window.innerWidth+1).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right})).slice(0,15)})); if(overflow.scroll>overflow.width+1)console.error(JSON.stringify(overflow)); assert.ok(overflow.scroll<=overflow.width+1);
      const before=calls.length;role='user';
      const ordinary=await context.newPage();ordinary.on('pageerror',e=>errors.push(e.message));
      await ordinary.goto(`${url}/admin-fixture`);
      await ordinary.getByRole('alert').waitFor();
      assert.equal(await ordinary.getByRole('navigation',{name:'Разделы администрирования'}).count(),0);
      assert.equal(calls.slice(before).filter(c=>c.endpoint.startsWith('/admin/')).length,0);
      assert.deepEqual(errors,[]);
      const report={passed:true,real_chromium:true,api_responses_mocked:true,admin_navigation:true,key_form_csrf_and_clear:true,separate_currency_display:true,job_errors_and_audit:true,keyboard_navigation:true,mobile_no_horizontal_overflow:true,ordinary_user_ui_denied:true,paid_calls:false,browser_version:browser.version()};
      fs.writeFileSync(path.join(directory,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
    });
  } finally {
    if(browser)await browser.close();const exited=new Promise(resolve=>vite.once('exit',resolve));vite.kill('SIGTERM');await exited;fs.closeSync(log);
  }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
