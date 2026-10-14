// Take screenshots of the app in a hidden browser (nothing appears on screen).
//   node tools/shot.mjs <outDir> home portfolio portfolio:all results:open activity:news settings:open help
//   PORT=8792 W=1040 H=700 node tools/shot.mjs <outDir> home        (another engine, another window size)
// After the colon come steps, joined by commas:
//   all | owned (Portfolio tabs)   news | did (Activity tabs)   open (open every "more" section)
//   drawer (click the first stock)   bottom   y600 (scroll to 600)   full (capture the whole page, not just the window)
// Run npm ci and npx playwright install chromium first.
import { chromium } from 'playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const [outDir, ...views] = process.argv.slice(2);
const width = +(process.env.W || 1320), height = +(process.env.H || 860), port = process.env.PORT || '8791';
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1, colorScheme: process.env.SCHEME || 'dark' });
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push('console: ' + m.text()); });
page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
page.on('requestfailed', (r) => problems.push('requestfailed: ' + r.url()));
if (process.env.GUIDE !== '1') await page.addInitScript(() => localStorage.setItem('nd.guide.done', '1'));
await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'load' });
await page.waitForTimeout(2500);
for (const v of views.length ? views : ['home']) {
  const [view, steps = ''] = v.split(':');
  await page.click(`[data-nav="${view}"]`);
  await page.waitForTimeout(1500);
  let full = false;
  for (const step of steps.split(',').filter(Boolean)) {
    if (step === 'all' || step === 'owned') await page.click(`[data-ptab="${step}"]`);
    else if (step === 'news' || step === 'did') await page.click(`[data-atab="${step}"]`);
    else if (step === 'open') await page.evaluate(() => document.querySelectorAll('#view details').forEach((d) => { d.open = true; }));
    else if (step === 'drawer') { await page.click('#view [data-sym]'); await page.waitForTimeout(2500); }
    else if (step === 'bottom') await page.evaluate(() => { const m = document.querySelector('#view'); m.scrollTop = m.scrollHeight; });
    else if (step.startsWith('y')) await page.evaluate((y) => { document.querySelector('#view').scrollTop = y; }, +step.slice(1));
    else if (step === 'full') full = true;
    await page.waitForTimeout(900);
  }
  const file = join(outDir, `${v.replace(/[:,]/g, '-')}.png`);
  if (full) {                                                    // the view scrolls inside the window: grow the window to fit it all
    const need = await page.evaluate(() => { const m = document.querySelector('#view'); return m.scrollHeight + (window.innerHeight - m.clientHeight); });
    await page.setViewportSize({ width, height: Math.min(Math.max(height, need), 9000) });
    await page.waitForTimeout(500);
    await page.screenshot({ path: file });
    await page.setViewportSize({ width, height });
  } else await page.screenshot({ path: file });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (overflow) problems.push(`${v}: page is wider than the window`);
  const clipped = await page.evaluate(() => [...document.querySelectorAll('#view .tile b, #view .mode b, #view h1')].filter((n) => n.scrollWidth > n.clientWidth + 1).map((n) => n.textContent.slice(0, 40)));
  if (clipped.length) problems.push(`${v}: text is cut off: ${clipped.join(' | ')}`);
}
await browser.close();
console.log(problems.length ? 'PROBLEMS\n' + [...new Set(problems)].join('\n') : 'no console errors');
