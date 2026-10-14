// Render the whole video, frame by frame, in hidden browsers, straight into ffmpeg. Nothing appears on screen.
//   node src/render.mjs                 every frame -> build/video.mp4 (no sound yet)
//   node src/render.mjs 0 300           only frames 0-299 -> build/video.mp4 (a quick test)
//   WORKERS=6 node src/render.mjs        how many browsers to run side by side
// Each worker draws its own stretch of frames (renderAt(f / fps)) and pipes JPEG pictures into its own
// ffmpeg, which makes build/seg_<k>.mp4. The pieces are then joined without re-encoding.
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';

const here = join(dirname(fileURLToPath(import.meta.url)), '..');
const FFMPEG = (process.env.FFMPEG || 'ffmpeg');
const TL = JSON.parse(readFileSync(join(here, 'build/timeline.json'), 'utf8'));
const fps = TL.fps, total = Math.round(TL.duration * fps);
const first = +(process.argv[2] || 0), last = Math.min(total, process.argv[3] ? +process.argv[3] : total);
const workers = +(process.env.WORKERS || 6);
const per = Math.ceil((last - first) / workers);
const t0 = Date.now();

async function worker(k, from, to) {
  const out = join(here, `build/seg_${k}.mp4`);
  const ff = spawn(FFMPEG, ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(fps), '-g', String(fps * 2), out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((ok, bad) => ff.on('close', (c) => (c === 0 ? ok() : bad(new Error(`ffmpeg ${k} exited ${c}`)))));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('file://' + join(here, 'src/stage.html'), { waitUntil: 'load' });
  await page.waitForFunction(() => window.stageReady === true && [...document.images].every((i) => i.complete), null, { timeout: 30000 });
  await page.evaluate(() => document.fonts.ready);
  const cdp = await page.context().newCDPSession(page);
  for (let f = from; f < to; f++) {
    await page.evaluate((t) => window.renderAt(t), f / fps);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 90, optimizeForSpeed: true });
    if (!ff.stdin.write(Buffer.from(data, 'base64'))) await new Promise((r) => ff.stdin.once('drain', r));
    if ((f - from) % 600 === 0 && f > from) console.log(`worker ${k}: ${f - from}/${to - from} frames, ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
  ff.stdin.end();
  await browser.close();
  await done;
  if (errors.length) throw new Error(`worker ${k} script errors: ${errors.join(' | ')}`);
  return out;
}

const jobs = [];
for (let k = 0; k < workers; k++) { const from = first + k * per, to = Math.min(last, from + per); if (from < to) jobs.push(worker(k, from, to)); }
const segs = await Promise.all(jobs);
const list = join(here, 'build/segments.txt');
writeFileSync(list, segs.map((s) => `file '${s}'`).join('\n') + '\n');
execFileSync(FFMPEG, ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', join(here, 'build/video.mp4')]);
for (const s of segs) rmSync(s, { force: true });
console.log(`rendered frames ${first}-${last - 1} (${last - first}) in ${((Date.now() - t0) / 60000).toFixed(1)} min -> build/video.mp4`);
