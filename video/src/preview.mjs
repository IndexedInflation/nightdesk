// Draw chosen moments of the video as still pictures, in a hidden browser, to check the layout.
//   node src/preview.mjs all                 every beat, just before it ends (plus a contact sheet per scene)
//   node src/preview.mjs 12.5 130 400.2      just these times (seconds)
//   node src/preview.mjs scene=idea          every beat of one scene
// Also reports script errors and any element timed to arrive outside its own scene.
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, rmSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(here, 'build/preview');
const args = process.argv.slice(2);
const TL = JSON.parse(readFileSync(join(here, 'build/timeline.json'), 'utf8'));
let jobs = [];
const beatJobs = (filter) => Object.entries(TL.beats).filter(([k]) => !filter || k.startsWith(filter + '.')).map(([k, b]) => ({ name: k, t: b.chunks ? Math.max(b.start + 0.2, b.end - 0.12) : (b.start + b.end) / 2 }));
if (args[0] === 'all') jobs = beatJobs(null);
else if (args[0] && args[0].startsWith('scene=')) jobs = beatJobs(args[0].slice(6));
else jobs = args.map((x) => ({ name: 't' + (+x).toFixed(2), t: +x }));
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
const problems = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') problems.push(`console ${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
await page.goto('file://' + join(here, 'src/stage.html'), { waitUntil: 'load' });
await page.waitForFunction(() => window.stageReady === true, null, { timeout: 20000 }).catch(() => problems.push('stage never became ready'));
await page.evaluate(() => document.fonts.ready);
const timing = await page.evaluate(() => {
  const bad = [];
  for (const s of window.Stage.scenes) for (const it of s.items) {
    const tag = (it.el.id ? '#' + it.el.id : it.el.tagName.toLowerCase()) + ' "' + (it.el.textContent || '').trim().slice(0, 30) + '"';
    if (it.at != null && (it.at < s.start - 0.01 || it.at > s.end - 0.2)) bad.push(`${s.id}: ${tag} arrives at ${it.at.toFixed(2)}, scene runs ${s.start.toFixed(2)}-${s.end.toFixed(2)}`);
    if (it.at != null && it.out != null && it.out < it.at + 0.3) bad.push(`${s.id}: ${tag} leaves (${it.out.toFixed(2)}) before it has arrived (${it.at.toFixed(2)})`);
  }
  return bad;
});
for (const j of jobs) {
  await page.evaluate((t) => window.renderAt(t), j.t);
  const over = await page.evaluate(() => { const c = document.getElementById('captext'); return c.offsetWidth > 1560 ? c.textContent : null; });
  if (over) problems.push(`${j.name}: caption too wide: ${over}`);
  await page.screenshot({ path: join(out, `${j.name}.jpg`), type: 'jpeg', quality: 82 });
}
await browser.close();
// contact sheets: one per scene, 3 across, so a whole chapter can be checked at a glance
if (jobs.length > 4) {
  const groups = {};
  for (const j of jobs) (groups[j.name.split('.')[0]] ||= []).push(j.name);
  for (const [scene, names] of Object.entries(groups)) {
    const list = names.map((n) => `file '${join(out, n + '.jpg')}'\nduration 1`).join('\n');
    const lst = join(out, `_${scene}.txt`);
    execFileSync('/bin/sh', ['-c', `printf '%s\n' "$1" > "$2"`, 'sh', list, lst]);
    const rows = Math.ceil(names.length / 3);
    execFileSync((process.env.FFMPEG || 'ffmpeg'), ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-vf', `scale=640:360,tile=3x${rows}:padding=6:color=white`, '-frames:v', '1', '-q:v', '3', join(out, `sheet-${scene}.jpg`)]);
  }
}
console.log(`${jobs.length} frames in build/preview`);
console.log(timing.length ? 'TIMING\n' + timing.join('\n') : 'timing: every element arrives inside its own scene');
console.log(problems.length ? 'PROBLEMS\n' + [...new Set(problems)].join('\n') : 'no script errors');
