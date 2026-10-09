// Diagnostic probe of the real wallpaper renderer; no external URLs.
const { chromium } = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const page = await browser.newPage({viewport:{width:2560,height:1440}, deviceScaleFactor:1.25});
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', e => { if(e.type()==='error') errors.push(e.text()); });
  await page.goto(pathToFileURL(path.resolve(process.argv[2])).href);
  await page.waitForTimeout(4500);
  const state = await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => {
    const c=document.querySelector('canvas'), g=c.getContext('webgl2')||c.getContext('webgl');
    const pixels=[];
    for(const xy of [[1,1],[c.width/4,c.height/2],[c.width/2,c.height/2],[c.width*0.9,c.height/2]]) {
      const p=new Uint8Array(4); g.readPixels(xy[0]|0,xy[1]|0,1,1,g.RGBA,g.UNSIGNED_BYTE,p); pixels.push([...p]);
    }
    resolve({inner:[innerWidth,innerHeight],canvas:[c.width,c.height],buffer:[g.drawingBufferWidth,g.drawingBufferHeight],viewport:[...g.getParameter(g.VIEWPORT)],error:g.getError(),pixels,renderer:g.getParameter(g.RENDERER)});
  })));
  await page.screenshot({path:process.argv[3]});
  console.log(JSON.stringify({state,errors}));
  await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
