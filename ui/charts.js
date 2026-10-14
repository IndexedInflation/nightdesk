// Charts drawn as plain SVG text. Marks follow one spec: 2px lines, 4px rounded bar ends,
// hairline grid, text always in ink colours (never the series colour), a legend for 2+ series,
// and a hover read-out on everything.
export const CH = new Map();      // chart id -> { W, left, right, n, tip(i) }
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const INK3 = '#7F8AA3', INK2 = '#B4BCCD', GRID = '#1D273A', SURFACE = '#0E1420';

export function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, raw = Math.pow(10, Math.floor(Math.log10(span / n)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * raw).find((s) => span / s <= n) || raw * 10;
  const out = []; for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out;
}
const legend = (series) => series.length < 2 ? '' :
  `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
const shell = (id, svg, series) => `${legend(series)}<div class="chart" data-chart="${id}">${svg}<div class="cross" data-keep></div><div class="tip" data-keep></div></div>`;

export function lineChart(id, { labels, series, fmt, log = false, height = 280, xTicks = [], zero = false, endLabels = true }) {
  const W = 760, H = height, m = { l: 50, r: endLabels ? 96 : 12, t: 10, b: 24 }, n = labels.length;
  const all = series.flatMap((s) => s.values).filter((v) => v != null && isFinite(v));
  if (n < 2 || !all.length) return '';
  const f = log ? Math.log : (v) => v;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (!log) { const pad = (hi - lo || Math.abs(hi) || 1) * 0.08; lo -= pad; hi += pad; }
  const x = (i) => m.l + (i / (n - 1)) * (W - m.l - m.r), y = (v) => m.t + (1 - (f(v) - f(lo)) / (f(hi) - f(lo))) * (H - m.t - m.b);
  const ticks = log ? [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64].filter((t) => t >= lo * 0.999 && t <= hi * 1.001) : niceTicks(lo, hi);
  let g = '';
  for (const t of ticks) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" stroke="${zero && t === 0 ? INK3 : GRID}"/>` +
    `<text x="${m.l - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end" font-size="11.5" fill="${INK3}">${esc(fmt(t))}</text>`;
  for (const t of xTicks) g += `<text x="${x(t.i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="11.5" fill="${INK3}">${esc(t.text)}</text>`;
  const ends = [];
  for (const s of series) {
    const pts = s.values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(' ');
    g += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    let last = s.values.length - 1; while (last > 0 && s.values[last] == null) last--;
    g += `<circle cx="${x(last).toFixed(1)}" cy="${y(s.values[last]).toFixed(1)}" r="4" fill="${s.color}" stroke="${SURFACE}" stroke-width="2"/>`;
    ends.push({ y: y(s.values[last]), text: fmt(s.values[last]) + (s.short ? ' ' + s.short : '') });
  }
  if (endLabels) { ends.sort((a, b) => a.y - b.y); for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    for (const e of ends) g += `<text x="${W - m.r + 9}" y="${(e.y + 4).toFixed(1)}" font-size="12" fill="${INK2}">${esc(e.text)}</text>`; }
  CH.set(id, { W, left: m.l, right: W - m.r, n, tip: (i) => `<div>${esc(labels[i])}</div>` +
    series.map((s) => s.values[i] == null ? '' : `<div><i style="background:${s.color}"></i>${esc(s.name)}: ${esc(fmt(s.values[i]))}</div>`).join('') });
  return shell(id, `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`, series);
}

export function barChart(id, { labels, series, fmt, height = 240, everyOther = false }) {
  const W = 760, H = height, m = { l: 50, r: 8, t: 12, b: 24 }, n = labels.length;
  const all = series.flatMap((s) => s.values).filter((v) => v != null && isFinite(v));
  if (!n || !all.length) return '';
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all), pad = (hi - lo || 1) * 0.07;
  const y = (v) => m.t + (1 - (v - (lo - pad)) / (hi - lo + 2 * pad)) * (H - m.t - m.b);
  const band = (W - m.l - m.r) / n, bw = Math.max(2, Math.min(24, (band - 8) / series.length - 2));
  let g = '';
  for (const t of niceTicks(lo, hi)) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" stroke="${GRID}"/>` +
    `<text x="${m.l - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end" font-size="11.5" fill="${INK3}">${esc(fmt(t))}</text>`;
  labels.forEach((lab, i) => {
    const cx = m.l + band * (i + 0.5), total = series.length * (bw + 2) - 2;
    series.forEach((s, j) => { const v = s.values[i]; if (v == null) return;
      const x0 = cx - total / 2 + j * (bw + 2), y0 = y(0), y1 = y(v), r = Math.min(4, Math.abs(y1 - y0), bw / 2), up = v >= 0 ? 1 : -1;
      g += `<path fill="${s.color}" d="M${x0.toFixed(1)},${y0.toFixed(1)}V${(y1 + up * r).toFixed(1)}Q${x0.toFixed(1)},${y1.toFixed(1)} ${(x0 + r).toFixed(1)},${y1.toFixed(1)}H${(x0 + bw - r).toFixed(1)}Q${(x0 + bw).toFixed(1)},${y1.toFixed(1)} ${(x0 + bw).toFixed(1)},${(y1 + up * r).toFixed(1)}V${y0.toFixed(1)}Z"/>`; });
    if (!everyOther || i % 2 === 0) g += `<text x="${cx.toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="11.5" fill="${INK3}">${esc(lab)}</text>`;
  });
  g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="${INK3}"/>`;
  CH.set(id, { W, left: m.l, right: W - m.r, n, banded: true, tip: (i) => `<div>${esc(labels[i])}</div>` +
    series.map((s) => s.values[i] == null ? '' : `<div><i style="background:${s.color}"></i>${esc(s.name)}: ${esc(fmt(s.values[i]))}</div>`).join('') });
  return shell(id, `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`, series);
}

export function candleChart(id, { bars, labels, xTicks = [], height = 250, width = 760, up = '#57C795', down = '#F0717A', fmt }) {
  const W = width, H = height, m = { l: 54, r: 8, t: 10, b: 24 }, n = bars.length;
  if (n < 2) return '';
  const lo0 = Math.min(...bars.map((b) => b.low)), hi0 = Math.max(...bars.map((b) => b.high)), pad = (hi0 - lo0 || 1) * 0.06, lo = lo0 - pad, hi = hi0 + pad;
  const band = (W - m.l - m.r) / n, bw = Math.max(1, Math.min(9, band - 2)), x = (i) => m.l + band * (i + 0.5), y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  let g = '';
  for (const t of niceTicks(lo, hi)) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" stroke="${GRID}"/>` +
    `<text x="${m.l - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end" font-size="11.5" fill="${INK3}">${esc(fmt(t))}</text>`;
  for (const t of xTicks) g += `<text x="${x(t.i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="11.5" fill="${INK3}">${esc(t.text)}</text>`;
  bars.forEach((b, i) => { const c = b.close >= b.open ? up : down, top = y(Math.max(b.open, b.close)), h = Math.max(1, Math.abs(y(b.open) - y(b.close)));
    g += `<line x1="${x(i).toFixed(1)}" x2="${x(i).toFixed(1)}" y1="${y(b.high).toFixed(1)}" y2="${y(b.low).toFixed(1)}" stroke="${c}"/>` +
      `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${c}"/>`; });
  CH.set(id, { W, left: m.l, right: W - m.r, n, banded: true, tip: (i) => { const b = bars[i], d = b.close >= b.open;
    return `<div>${esc(labels[i])}</div><div>Opened ${esc(fmt(b.open))}, closed ${esc(fmt(b.close))} (${d ? 'up' : 'down'})</div><div>High ${esc(fmt(b.high))}, low ${esc(fmt(b.low))}</div>`; } });
  return shell(id, `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`, []);
}

// One pair of listeners serves every chart on the page.
export function wireCharts(root) {
  root.addEventListener('pointermove', (ev) => {
    const host = ev.target.closest && ev.target.closest('[data-chart]'); if (!host) return;
    const c = CH.get(host.dataset.chart), svg = host.querySelector('svg'); if (!c || !svg) return;
    const box = svg.getBoundingClientRect(), px = ((ev.clientX - box.left) / box.width) * c.W, span = c.right - c.left;
    const i = c.banded ? Math.max(0, Math.min(c.n - 1, Math.floor(((px - c.left) / span) * c.n)))
                       : Math.max(0, Math.min(c.n - 1, Math.round(((px - c.left) / span) * (c.n - 1))));
    const cx = c.banded ? c.left + (span / c.n) * (i + 0.5) : c.left + (span * i) / (c.n - 1);
    const tip = host.querySelector('.tip'), cross = host.querySelector('.cross'), left = (cx / c.W) * box.width;
    tip.innerHTML = c.tip(i); tip.style.opacity = 1; cross.style.opacity = c.banded ? 0 : 0.7; cross.style.left = left + 'px';
    const tw = tip.offsetWidth; tip.style.left = Math.max(0, Math.min(left + 12, box.width - tw - 4)) + 'px'; tip.style.top = '6px';
  });
  root.addEventListener('pointerleave', hide, true);
  function hide(ev) { const host = ev.target.closest && ev.target.closest('[data-chart]'); if (!host || host.contains(ev.relatedTarget)) return;
    host.querySelector('.tip').style.opacity = 0; host.querySelector('.cross').style.opacity = 0; }
}
