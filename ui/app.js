// Nightdesk's window (version 2: built to be read at a glance).
// One state object, one render() that turns it into HTML, and a small "morph" step that
// changes only what differs so nothing flickers.
import { lineChart, barChart, candleChart, wireCharts } from './charts.js';

const OLD = { tonight: 'home', rankings: 'portfolio', news: 'activity', record: 'results', thinks: 'help' };
const saved = localStorage.getItem('nd.view');
const S = { snap: null, view: OLD[saved] || saved || 'home', activity: [], board: null, curve: null, research: null, news: null,
  settings: null, limits: {}, detail: null, filter: '', showAll: false, actFilter: 'main', confirm: null, span: 'session', offline: true,
  ptab: 'owned', psort: 'value', atab: 'did', resetAmount: null, guide: !localStorage.getItem('nd.guide.done'), modes: null, defaultMode: 'steady', readerPython: '', readerModel: '' };
const C = { s1: '#c98500', s2: '#3987e5', s3: '#d55181' };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const DASH = '–', MINUS = '−';
const money = (v) => (v == null ? DASH : usd.format(v));
const money0 = (v) => (v == null ? DASH : usd0.format(v));
const signMoney = (v, f = usd) => (v == null ? DASH : (v >= 0 ? '+' : MINUS) + f.format(Math.abs(v)));
const pct = (v, d = 2) => { if (v == null) return DASH; const t = (Math.abs(v) * 100).toFixed(d); return (+t === 0 ? '' : v >= 0 ? '+' : MINUS) + t + '%'; };
const pct0 = (v) => (v == null ? DASH : Math.round(v * 100) + '%');
const num = (v, d = 2) => (v == null ? DASH : (v < 0 ? MINUS : '') + Math.abs(v).toFixed(d));
const tone = (v) => (v == null || Math.abs(v) < 1e-9 ? '' : v > 0 ? 'up' : 'down');
const clock = (zone) => (ts, extra = {}) => new Intl.DateTimeFormat('en-AU', { timeZone: zone, hour: 'numeric', minute: '2-digit', ...extra }).format(new Date(ts * 1000));
const LOCAL_ZONE = new Intl.DateTimeFormat().resolvedOptions().timeZone;
const tLocal = clock(LOCAL_ZONE), tNY = clock('America/New_York');
const sessionLocal = (iso) => tLocal(Date.parse(iso) / 1000, { weekday: 'short' });
const localDay = (ts) => new Intl.DateTimeFormat('en-AU', { timeZone: LOCAL_ZONE, weekday: 'long', day: 'numeric', month: 'long' }).format(new Date(ts * 1000));
const dur = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`; };
const longDate = (iso) => new Date(iso + 'T12:00:00').toLocaleDateString('en-AU', { day: 'numeric', month: 'long', year: 'numeric' });
const pill = (kind, text) => `<span class="pill ${kind}"><i></i>${esc(text)}</span>`;
const what = (text) => `<p class="what">${esc(text)}</p>`;
const ICON = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/>', portfolio: '<path d="M12 3 3 7.5l9 4.5 9-4.5z"/><path d="m3 12 9 4.5 9-4.5"/><path d="m3 16.5 9 4.5 9-4.5"/>',
  results: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>', activity: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  settings: '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/><path d="M4 12h2"/><path d="M10 12h10"/><circle cx="8" cy="12" r="2"/><path d="M4 18h10"/><path d="M18 18h2"/><circle cx="16" cy="18" r="2"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7"/><path d="M12 17h.01"/>' };
const icon = (k) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Nightdesk': '1' }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt), j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
  return j;
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3200); }

// ---------- morph: make `root` look like `html`, touching as little as possible ----------
function morph(root, html) { const t = document.createElement('template'); t.innerHTML = html.trim(); sync(root, t.content); }
function sync(a, b) {
  let x = a.firstChild, y = b.firstChild;
  while (y) { const next = y.nextSibling;
    if (!x) a.appendChild(y);
    else if (x.nodeType === y.nodeType && (x.nodeType !== 1 || (x.tagName === y.tagName && x.dataset.key === y.dataset.key))) { patch(x, y); x = x.nextSibling; }
    else { a.replaceChild(y, x); x = y.nextSibling; }
    y = next; }
  while (x) { const n = x.nextSibling; a.removeChild(x); x = n; }
}
function patch(x, y) {
  if (x.nodeType !== 1) { if (x.nodeValue !== y.nodeValue) x.nodeValue = y.nodeValue; return; }
  if (x.hasAttribute('data-keep')) return;
  const open = x.tagName === 'DETAILS' && x.open;                       // a section the person opened stays open across redraws
  for (const { name } of [...x.attributes]) if (!y.hasAttribute(name)) x.removeAttribute(name);
  for (const { name, value } of [...y.attributes]) if (x.getAttribute(name) !== value) x.setAttribute(name, value);
  if (open) x.open = true;
  if (x.tagName === 'INPUT') { if (document.activeElement !== x) { if (x.type === 'checkbox') x.checked = y.hasAttribute('checked'); else if (y.hasAttribute('value')) x.value = y.getAttribute('value'); } return; }
  sync(x, y);
}

// ---------- top strip and rail ----------
function topBar() {
  const s = S.snap, now = Date.now() / 1000, open = s && s.clock.phase === 'open';
  let feed = '';
  if (S.offline || !s) feed = pill('bad', 'Engine not answering');
  else if (s.setup && !s.setup.network_enabled) feed = pill('', 'Offline · setup available');
  else if (open && s.engine.ready) { const f = s.feed;
    feed = !f.lines_up ? pill('bad', 'Price feed is down') : f.age != null && f.age > 20 ? pill('bad', `No prices for ${dur(f.age)}`)
      : f.median_ms != null ? pill('', `Prices ${(f.median_ms / 1000).toFixed(1)} s behind New York`) : pill('', 'Waiting for prices'); }
  const lit = open && s.engine.auto_trade && !s.engine.killed;
  return `<div class="brand"><span class="lampmark ${lit ? '' : 'off'}"></span>Nightdesk</div>
    <span>Your time <b>${tLocal(now)}</b></span><span>New York <b>${tNY(now)}</b></span>${feed}${pill('paper', 'Pretend money')}`;
}
const VIEWS = [['home', 'Home'], ['portfolio', 'Portfolio'], ['results', 'Results'], ['activity', 'Activity'], ['settings', 'Settings']];
function nav() {
  const a = S.snap && S.snap.account, b = (id, label, cls = '') => `<button data-nav="${id}" class="${cls}" ${S.view === id ? 'aria-current="page"' : ''}>${icon(id)}${label}</button>`;
  return VIEWS.map(([id, label]) => b(id, label)).join('') + `<div class="gap"></div>` + b('help', 'How it works') +
    `<div class="railfoot">Practice account<b>${a ? money0(a.equity) : DASH}</b>${a ? `<span class="${tone(a.total_pnl)}">${signMoney(a.total_pnl, usd0)} since the start</span>` : ''}</div>`;
}

function setupPanel() {
  const q = S.snap && S.snap.setup; if (!q) return '';
  const busy = q.busy, ready = q.ready;
  return `<div class="card setup" data-key="setup"><h2>${ready ? 'Your local connection' : 'Set up Nightdesk on this computer'}</h2>
    <p>${ready ? 'Your own forecaster and price history are ready. Practice trading starts only when you switch it on.' : 'Nightdesk starts offline. Allow it to download public stock lists from Wikipedia and prices from Yahoo Finance, then train the forecaster locally. No Yahoo account or broker login is needed.'}</p>
    ${!ready ? '<p class="muted">Initial downloads and training can take an hour or more and need several GB of memory and disk space. The app stays open while setup runs. There is no pretrained model to download.</p>' : ''}
    <div class="row">${pill(q.network_enabled ? '' : 'paper', q.network_enabled ? 'Public data allowed' : 'Offline')}
      ${!ready ? `<button class="btn primary" data-act="setup" ${busy ? 'disabled' : ''}>${busy ? 'Setup running…' : q.error ? 'Retry setup' : 'Allow public downloads and train my forecaster'}</button>` : ''}
      ${q.network_enabled ? '<button class="btn" data-act="disconnect">Go offline and pause practice trading</button>' : ''}</div>
    ${busy ? `<p role="status" aria-live="polite">${esc(q.progress || q.message)}</p>` : ''}
    ${q.error ? `<p class="note warn" role="alert">${esc(q.error)}</p>` : ''}
    <p class="muted">Your downloaded data, models, settings and practice account stay in your user data folder. They are separate from the shared source code.</p></div>`;
}

// ---------- Home ----------
function status(s) {
  const c = s.clock, e = s.engine, chk = S.settings ? S.settings.rebalance_minutes_before_close : 15;
  if (!e.ready) return ['', 'Setup', e.message || 'Prepare your own local data and forecaster.'];
  if (e.killed) return ['stop', 'Stopped by the safety switch', 'The account fell past its limit, so everything was sold. Turn trading back on in Settings when you are ready.'];
  if (!e.auto_trade) return ['', 'Paused', 'It is still watching and ranking, but it will not buy or sell until you resume.'];
  if (c.phase === 'open') { const close = Date.parse(c.session_close) / 1000, check = close - chk * 60, before = Date.now() / 1000 < check;
    return ['live', 'Trading now', `New York closes in ${dur(c.seconds_to_change)}. ${e.halt_today ? 'Buying is paused for today after the daily loss limit.' : before ? `It checks the portfolio once, at ${tLocal(check)}.` : 'Today’s portfolio check is done.'}`]; }
  if (c.phase === 'pre') return ['', 'Waiting for New York to open', `Opens in ${dur(c.seconds_to_change)} (${sessionLocal(c.session_open)} your local time).`];
  return ['', 'Market closed', `It trades again when New York opens: ${sessionLocal(c.session_open)} your local time, in ${dur(c.seconds_to_change)}.`];
}
function dayBar(s) {
  const c = s.clock, open = Date.parse(c.session_open) / 1000, close = Date.parse(c.session_close) / 1000, now = Date.now() / 1000;
  const chk = close - (S.settings ? S.settings.rebalance_minutes_before_close : 15) * 60, at = (t) => Math.max(0, Math.min(100, ((t - open) / (close - open)) * 100));
  if (c.phase !== 'open') return `<div class="daybar"><div class="ends"><span>Next session opens <b>${esc(sessionLocal(c.session_open))}</b></span><span>Portfolio check <b>${tLocal(chk)}</b></span><span>Closes <b>${esc(sessionLocal(c.session_close))}</b></span></div></div>`;
  return `<div class="daybar"><div class="track"><i class="done" style="width:${at(now).toFixed(1)}%"></i>
      <div class="pin" style="left:${at(chk).toFixed(1)}%"><span>Check ${tLocal(chk)}</span></div><div class="pin now" style="left:${at(now).toFixed(1)}%"><span>Now</span></div></div>
    <div class="ends"><span>Opened ${tLocal(open)}</span><span>Closes ${tLocal(close)}</span></div></div>`;
}
function curveChart() {
  const pts = (S.curve && S.curve.points) || [];
  if (pts.length < 3) return `<p class="empty">The line appears once New York is open and prices are flowing.</p>`;
  const e0 = pts[0][1], s0 = (pts.find((p) => p[2]) || [])[2], day = S.span !== 'session';
  const labels = pts.map((p) => tLocal(p[0], day ? { weekday: 'short', day: 'numeric', month: 'short' } : {}));
  const xTicks = []; let last = '';
  pts.forEach((p, i) => { const k = day ? new Date(p[0] * 1000).toDateString() : Math.floor(p[0] / 3600); if (k !== last) { if (last !== '' || i === 0) xTicks.push({ i, text: day ? tLocal(p[0], { hour: undefined, minute: undefined, day: 'numeric', month: 'short' }) : tLocal(p[0]) }); last = k; } });
  const keep = xTicks.filter((_, i) => i % Math.ceil(xTicks.length / 8) === 0);
  return lineChart('curve', { labels, zero: true, fmt: (v) => pct(v, 2), xTicks: keep, height: 230,
    series: [{ name: 'Your practice account', short: 'You', color: C.s1, values: pts.map((p) => p[1] / e0 - 1) },
             { name: 'S&P 500 (the market)', short: 'Market', color: C.s2, values: pts.map((p) => (p[2] && s0 ? p[2] / s0 - 1 : null)) }] });
}
const plBar = (frac, scale) => { const w = Math.min(50, (Math.abs(frac) / scale) * 50).toFixed(1); return `<div class="pl" title="Change since it was bought">${frac >= 0 ? `<i class="pos" style="width:${w}%"></i>` : `<i class="neg" style="width:${w}%"></i>`}</div>`; };
const CHIP = { trade: (a) => (a.data && a.data.side === 'sell' ? ['sell', 'Sold'] : ['buy', 'Bought']), decision: () => ['', 'Decision'], risk: () => ['risk', 'Safety'], news: () => ['news', 'Headline'], user: () => ['you', 'You'], system: () => ['', 'Engine'] };
const chip = (a) => { const [cls, label] = (CHIP[a.kind] || CHIP.system)(a); return `<span class="chip ${cls}">${label}</span>`; };
function feedList(items, grouped = false) {
  if (!items.length) return `<p class="empty">Nothing yet. Every trade and decision will be explained here in plain words.</p>`;
  let out = '', lastDay = '';
  for (const it of items) { const d = localDay(it.ts);
    if (grouped && d !== lastDay) { out += `${lastDay ? '</ol>' : ''}<div class="daylabel">${esc(d)}</div><ol class="feed">`; lastDay = d; }
    else if (!grouped && !out) out = '<ol class="feed">';
    out += `<li class="${esc(it.kind)}" data-key="${it.id}"><time>${tLocal(it.ts)}</time><div><b>${chip(it)}${esc(it.title)}</b><span class="sel">${esc(it.detail)}</span></div></li>`; }
  return out + '</ol>';
}
function homeView() {
  if (S.snap && S.snap.setup && !S.snap.engine.ready) return `<div class="wrap"><h1 class="say">Welcome to Nightdesk.</h1>${setupPanel()}<p class="note">Practice money only. After setup, review Settings and resume practice trading when you are ready.</p></div>`;
  const s = S.snap; if (!s) return `<div class="wrap"><div class="state"><span class="dot"></span><div><h1>Connecting to the engine</h1><p>If this stays for more than a minute, the engine isn’t running. Quit Nightdesk and open it again.</p></div></div></div>`;
  const a = s.account, e = s.engine, open = s.clock.phase === 'open', [cls, head, sub] = status(s);
  const mk = S.settings ? modeOf(S.settings) : null;
  const act = `<div class="acts">` + (mk ? `<button class="btn quiet" data-nav="settings" title="How much risk it takes. Change it in Settings.">Risk: ${mk === 'custom' ? 'custom' : MODE_COPY[mk][0]}</button>` : '') +
    (!e.ready ? '' : e.auto_trade && !e.killed ? `<button class="btn" data-act="pause">Pause</button>` : `<button class="btn primary" data-act="resume">Resume trading</button>`) + `</div>`;
  const gap = s.spy.day_pct == null ? null : a.day_pnl_pct - s.spy.day_pct;
  const tiles = `<div class="tiles">
    <div class="tile" title="Cash plus what the stocks are worth right now"><label>Account value</label><b>${money0(a.equity)}</b><small>Started with ${money0(a.starting_cash)}, all pretend</small></div>
    <div class="tile" title="Change since New York opened"><label>${open ? 'Today so far' : 'Last session'}</label><b class="${tone(a.day_pnl)}">${signMoney(a.day_pnl, usd0)}</b><small>${pct(a.day_pnl_pct)}${gap == null ? '' : `, ${Math.abs(gap) < 0.00005 ? 'level with' : gap > 0 ? pct(gap).slice(1) + ' ahead of' : pct(gap).slice(1) + ' behind'} the market`}</small></div>
    <div class="tile" title="Change since the practice account began"><label>Since the start</label><b class="${tone(a.total_pnl)}">${signMoney(a.total_pnl, usd0)}</b><small>${pct(a.total_pnl_pct)} over ${a.sessions || 1} ${a.sessions === 1 || !a.sessions ? 'session' : 'sessions'}</small></div>
    <div class="tile" title="How much of the account is in stocks; the rest is cash"><label>Invested</label><b>${pct0(a.exposure)}</b><small>${a.positions} stocks, ${money0(a.cash)} in cash</small></div></div>`;
  const scale = Math.max(0.02, ...s.positions.map((p) => Math.abs(p.pnl_pct)));
  const movers = [...s.positions].sort((x, y) => Math.abs(y.pnl) - Math.abs(x.pnl)).slice(0, 8);
  const owns = s.positions.length ? `<ul class="mini">${movers.map((p) => `<li tabindex="0" role="button" data-sym="${esc(p.symbol)}" data-key="${esc(p.symbol)}"><div class="who" style="display:flex"><b>${esc(p.symbol)}</b><span>${esc(p.name)}</span></div>${plBar(p.pnl_pct, scale)}<div class="amt ${tone(p.pnl)}">${signMoney(p.pnl, usd0)}</div></li>`).join('')}</ul>`
    : `<p class="empty">Nothing yet. ${!e.auto_trade ? 'Practice trading is paused. Resume when you are ready.' : open ? 'It buys once prices for the whole list have arrived.' : 'It buys when New York next opens.'}</p>`;
  const spans = [['session', open ? 'Today' : 'Last session'], ['week', 'Week'], ['all', 'All']];
  const guide = S.guide ? `<div class="guide"><b>New here? Three things to know.</b><ol>
      <li><b>It is practising with pretend money.</b> Nothing here can touch real money.</li>
      <li><b>It trades once a day,</b> about 15 minutes before New York closes. The rest of the time it watches.</li>
      <li><b>Green and red are pretend profit and loss.</b> The blue line on the chart is the market, the thing to beat.</li></ol>
      <button class="btn" data-act="guide">Got it</button></div>` : '';
  return `<div class="wrap">${guide}<div class="state ${cls}"><span class="dot"></span><div><h1>${esc(head)}</h1><p>${esc(sub)}</p></div><span class="spacer"></span>${act}</div>
    ${tiles}
    <div class="card"><div class="spread" style="align-items:center"><h2>The trading day, in your local time</h2></div>${dayBar(s)}</div>
    <div class="card"><div class="spread" style="align-items:center"><h2>You against the market</h2><div class="seg">${spans.map(([k, l]) => `<button data-cspan="${k}" aria-pressed="${S.span === k}">${l}</button>`).join('')}</div></div>${curveChart()}</div>
    <div class="cols2"><div class="card"><div class="spread" style="align-items:baseline"><h2>Biggest movers it owns</h2><button class="btn" data-nav="portfolio">See all ${s.positions.length}</button></div>${owns}
        ${s.pending.length ? `<p class="muted" style="margin:10px 0 0">${s.pending.length} ${s.pending.length === 1 ? 'order is' : 'orders are'} waiting for the next price.</p>` : ''}</div>
      <div class="card"><div class="spread" style="align-items:baseline"><h2>What it just did</h2><button class="btn" data-nav="activity">See everything</button></div>${feedList(S.activity.filter((x) => x.kind !== 'system').slice(0, 5))}</div></div></div>`;
}

// ---------- Portfolio ----------
function portfolioView() {
  const s = S.snap, b = S.board;
  const tabs = `<div class="tabs" role="tablist"><button role="tab" data-ptab="owned" aria-selected="${S.ptab === 'owned'}">What it owns${s ? ` (${s.positions.length})` : ''}</button><button role="tab" data-ptab="all" aria-selected="${S.ptab === 'all'}">Every stock, ranked${b ? ` (${b.rows.length})` : ''}</button></div>`;
  if (S.ptab === 'owned') {
    if (!s) return `<div class="wrap">${tabs}<p class="empty">Connecting.</p></div>`;
    if (!s.positions.length) return `<div class="wrap"><h1 class="say">Portfolio</h1>${tabs}<p class="empty">It owns nothing yet. It buys when New York is open and prices for the whole list have arrived.</p></div>`;
    const sort = { value: (x, y) => y.value - x.value, best: (x, y) => y.pnl_pct - x.pnl_pct, worst: (x, y) => x.pnl_pct - y.pnl_pct, rank: (x, y) => (x.rank || 999) - (y.rank || 999) }[S.psort];
    const rows = [...s.positions].sort(sort), scale = Math.max(0.02, ...rows.map((p) => Math.abs(p.pnl_pct)));
    const bySector = {}; rows.forEach((p) => { bySector[p.sector || 'Other'] = (bySector[p.sector || 'Other'] || 0) + p.weight; });
    const sect = Object.entries(bySector).sort((x, y) => y[1] - x[1]), top = sect[0] ? sect[0][1] : 1;
    const sorts = [['value', 'Biggest holdings'], ['best', 'Best'], ['worst', 'Worst'], ['rank', 'Best ranked']];
    return `<div class="wrap"><h1 class="say">What it owns, and how each is doing.</h1>${what('Each stock gets an equal slice. Click any row for its price chart and the reasons the forecaster likes it.')}${tabs}
      <div class="cols2" style="margin-top:0"><div class="card"><h2>Where the money is</h2><div class="sect">${sect.map(([k, v]) => `<span>${esc(k)}</span><div class="b" style="width:${((v / top) * 100).toFixed(0)}%"></div><span class="v">${pct0(v)}</span>`).join('')}
          <span>Cash</span><div class="b" style="width:${(((1 - s.account.exposure) / top) * 100).toFixed(0)}%;background:var(--paper-3)"></div><span class="v">${pct0(1 - s.account.exposure)}</span></div></div>
        <div class="card"><h2>In short</h2><div class="kv"><span>Stocks owned</span><span>${rows.length}</span><span>Worth</span><span>${money(s.account.invested)}</span><span>Cash</span><span>${money(s.account.cash)}</span>
          <span>Up since bought</span><span>${rows.filter((p) => p.pnl > 0).length} of ${rows.length}</span><span>Pretend profit so far</span><span class="${tone(s.account.total_pnl)}">${signMoney(s.account.total_pnl)}</span><span>Trading costs paid</span><span>${money(s.account.costs)}</span></div></div></div>
      <div class="row" style="margin:16px 0 8px"><span class="muted">Sort by</span><div class="seg">${sorts.map(([k, l]) => `<button data-psort="${k}" aria-pressed="${S.psort === k}">${l}</button>`).join('')}</div></div>
      <table class="tbl"><thead><tr><th>Stock</th><th class="n">Worth</th><th class="n">Price</th><th class="n">Today</th><th style="width:150px;padding-left:16px">Since bought</th><th class="n"></th><th class="n">Rank today</th></tr></thead><tbody>
      ${rows.map((p) => `<tr class="click" tabindex="0" role="button" data-sym="${esc(p.symbol)}" data-key="${esc(p.symbol)}"><td><span class="sym">${esc(p.symbol)}</span><span class="co">${esc(p.name)}</span></td>
        <td class="n">${money0(p.value)}<small>${(p.weight * 100).toFixed(1)}% of the account</small></td><td class="n">${money(p.last)}</td><td class="n ${tone(p.day_pct)}">${pct(p.day_pct)}</td>
        <td style="padding-left:16px">${plBar(p.pnl_pct, scale)}</td><td class="n ${tone(p.pnl)}">${signMoney(p.pnl)}<small>${pct(p.pnl_pct)}</small></td><td class="n">${p.rank || DASH}</td></tr>`).join('')}</tbody></table></div>`;
  }
  if (!b || !b.rows.length) return `<div class="wrap">${tabs}<p class="empty">The rankings appear once the engine has scored the list.</p></div>`;
  const q = S.filter.trim().toLowerCase();
  let rows = b.rows.filter((r) => !q || r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || r.sector.toLowerCase().includes(q));
  const total = rows.length; if (!S.showAll && !q) rows = rows.slice(0, 100);
  const when = b.live ? `Updated ${tLocal(b.at)} from live prices.` : `As of the last closing bell.`;
  return `<div class="wrap"><h1 class="say">Every stock, best score first.</h1>${what(`A higher score means the forecaster expects the stock to do better than the others over the next few weeks. It is a ranking, not a price target. ${when}`)}${tabs}
    <div class="row" style="margin-bottom:10px"><input class="search" type="search" placeholder="Find a company, ticker or industry" value="${esc(S.filter)}" data-search aria-label="Find a stock"><span class="muted">${total} ${total === 1 ? 'stock' : 'stocks'}</span></div>
    <table class="tbl"><thead><tr><th style="width:54px">Rank</th><th>Stock</th><th>Industry</th><th class="n">Score</th><th class="n">Price</th><th class="n">Today</th><th class="why">In its favour</th><th>Owned</th></tr></thead><tbody>` +
    rows.map((r) => `<tr class="click" tabindex="0" role="button" data-sym="${esc(r.symbol)}" data-key="${esc(r.symbol)}"><td class="num">${r.rank}</td><td><span class="sym">${esc(r.symbol)}</span><span class="co">${esc(r.name)}</span></td>
      <td class="soft">${esc(r.sector)}</td><td class="n">${num(r.score)}</td><td class="n">${money(r.last)}</td><td class="n ${tone(r.day_pct)}">${pct(r.day_pct)}</td>
      <td class="why">${esc(((r.why && r.why.for) || []).slice(0, 2).join('; '))}</td><td>${r.held ? '<span class="held" title="Owned"></span>' : ''}</td></tr>`).join('') +
    `</tbody></table>${!S.showAll && !q && total > 100 ? `<p style="margin-top:14px"><button class="btn" data-act="showall">Show all ${total}</button></p>` : ''}</div>`;
}

// ---------- stock drawer ----------
function drawer() {
  const d = S.detail; if (!d) return '';
  const row = (S.board && S.board.rows.find((r) => r.symbol === d.symbol)) || {}, pos = S.snap && S.snap.positions.find((p) => p.symbol === d.symbol);
  const name = row.name || (pos && pos.name) || d.symbol, last = (pos && pos.last) || row.last, dayp = pos ? pos.day_pct : row.day_pct, why = row.why || (pos && pos.why) || {};
  let chart = `<p class="empty">Loading prices.</p>`;
  if (d.bars && d.bars.length > 1) {
    let bars = d.bars, labels, xTicks = [];
    if (d.span === 'day') { const g = []; for (let i = 0; i < bars.length; i += 5) { const c = bars.slice(i, i + 5); g.push({ time: c[0].time, open: c[0].open, close: c[c.length - 1].close, high: Math.max(...c.map((b) => b.high)), low: Math.min(...c.map((b) => b.low)) }); }
      bars = g; labels = bars.map((b) => tLocal(b.time) + ' local'); let lh = null; bars.forEach((b, i) => { const h = Math.floor(b.time / 3600); if (h !== lh) { if (lh !== null) xTicks.push({ i, text: tLocal(b.time) }); lh = h; } });
    } else { labels = bars.map((b) => b.time); let lm = null; bars.forEach((b, i) => { const mth = b.time.slice(0, 7); if (mth !== lm) { if (lm !== null) xTicks.push({ i, text: new Date(b.time).toLocaleDateString('en-AU', { month: 'short' }) }); lm = mth; } }); }
    const span = Math.max(...bars.map((b) => b.high)) - Math.min(...bars.map((b) => b.low));
    chart = candleChart('candles', { bars, labels, xTicks: xTicks.filter((_, i) => i % Math.ceil(xTicks.length / 5) === 0), width: 430, height: 230, fmt: (v) => '$' + (span < 5 ? v.toFixed(2) : v.toFixed(0)) });
  } else if (d.bars) chart = `<p class="empty">No price bars for this span yet.</p>`;
  const spans = [['day', 'Latest day'], ['quarter', '3 months'], ['year', '1 year']];
  const list = (items) => (items && items.length ? `<ul>${items.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : `<p class="muted">Not near the top of the list, so no breakdown is kept.</p>`);
  return `<button class="btn close" data-close>Close</button><h2>${esc(d.symbol)}</h2><div class="co">${esc(name)}${row.sector ? ', ' + esc(row.sector) : ''}</div>
    <div class="row" style="margin:14px 0 4px;align-items:baseline"><span class="price">${money(last)}</span><span class="${tone(dayp)}">${pct(dayp)} today</span></div>
    <p class="soft">${row.rank ? `Ranked ${row.rank} of ${S.board.rows.length} today.` : 'Not on today’s list.'} ${pos ? 'It owns this one.' : 'It does not own this one.'}</p>
    <div class="seg" style="margin:6px 0 10px">${spans.map(([k, l]) => `<button data-span="${k}" aria-pressed="${d.span === k}">${l}</button>`).join('')}</div>${chart}
    ${pos ? `<h3>Your holding</h3><div class="kv"><span>Shares</span><span>${pos.qty.toFixed(2)}</span><span>Paid, on average</span><span>${money(pos.avg_cost)}</span>
      <span>Worth now</span><span>${money(pos.value)}</span><span>Since bought</span><span class="${tone(pos.pnl)}">${signMoney(pos.pnl)} (${pct(pos.pnl_pct)})</span>
      <span>Bought</span><span>${tLocal(pos.since, { weekday: 'short', day: 'numeric', month: 'short' })}</span></div>` : ''}
    <h3>What the forecaster likes about it</h3>${list(why.for)}<h3>What counts against it</h3>${list(why.against)}
    <p class="muted" style="font-size:12.5px">These are the measurements that moved this stock’s score the most. They describe patterns in prices and events, not a view on the company.</p>`;
}
async function openDetail(symbol, span = 'day') {
  S.detail = { symbol, span, bars: null }; render();
  try { const bars = await api(`/api/bars?symbol=${encodeURIComponent(symbol)}&span=${span}`); if (S.detail && S.detail.symbol === symbol && S.detail.span === span) { S.detail.bars = bars; render(); } }
  catch (err) { if (S.detail) { S.detail.bars = []; render(); } }
}

// ---------- Results ----------
const cmpRow = (label, value, max, color, text) => `<span class="t">${label}</span><div class="b" style="width:${Math.max(1, (Math.abs(value) / max) * 100).toFixed(1)}%;background:${color}"></div><span class="v">${text}</span>`;
function fairCharts(F) {
  const long = F.curve.dates.length > 200, xt = []; let ly = '';
  F.curve.dates.forEach((d, k) => { const y = d.slice(0, 4); if (y !== ly) { if (ly && (!long || +y % 2 === 0)) xt.push({ i: k, text: y }); ly = y; } });
  const beat = F.yearly.filter((r) => r.strategy > r.spy).length;
  return `<figure>${lineChart('fair-growth', { labels: F.curve.dates, log: long, fmt: (v) => '$' + (v >= 10 ? v.toFixed(0) : v.toFixed(2)), xTicks: xt, height: 290,
      series: [{ name: 'Nightdesk', short: 'Nightdesk', color: C.s1, values: F.curve.strategy }, { name: 'S&P 500', short: 'S&P 500', color: C.s2, values: F.curve.spy }, { name: 'Every member equally', short: 'All members', color: C.s3, values: F.curve.universe }] })}
      <figcaption>Growth of $1, after trading costs.${long ? ' The scale doubles at each line.' : ''}</figcaption></figure>
    <figure>${barChart('fair-years', { labels: F.yearly.map((r) => r.year), fmt: (v) => (v * 100).toFixed(0) + '%',
      series: [{ name: 'Nightdesk', color: C.s1, values: F.yearly.map((r) => r.strategy) }, { name: 'S&P 500', color: C.s2, values: F.yearly.map((r) => r.spy) }] })}
      <figcaption>Return in each calendar year. Nightdesk beat the S&amp;P 500 in ${beat} of ${F.yearly.length} years.</figcaption></figure>`;
}
function resultsView() {
  const R = S.research, F = R && R.pit_rehearsal, X = R && R.pit_exam, D = R && R.design, P = (R && R.pit) || {}, M = (R && R.modes) || null;
  if (!F) return `<div class="wrap"><h1 class="say">Results</h1><p class="sub">The test results aren’t on disk yet.</p></div>`;
  const mine = S.settings ? modeOf(S.settings) : null, key = M ? (M[mine] ? mine : M.default) : null;       // the risk setting this page talks about
  const A = key ? M[key] : F.strategy, B = M ? M.spy : F.spy, U = F.universe, E = F.real_equal_weight_fund, name = key ? MODE_COPY[key][0] : '';
  const gap = A.cagr - B.cagr, calmer = Math.abs(A.max_drawdown) < Math.abs(B.max_drawdown) - 0.03;
  const head = gap > 0.004 ? `In a fair test it finished ahead of the market${calmer ? ', with smaller falls' : ''}.` : gap > -0.01 ? `In a fair test it kept pace with the market${calmer ? ', with smaller falls' : ''}.` : `In a fair test it made less than the market${calmer ? ', and fell less' : ''}.`;
  const maxR = Math.max(A.cagr, B.cagr), maxD = Math.max(Math.abs(A.max_drawdown), Math.abs(B.max_drawdown));
  const one = (v) => (v * 100).toFixed(1) + '%', all = (l) => (l.beaten_cagr === l.runs ? 'all ' + l.runs : l.beaten_cagr + ' of ' + l.runs);
  const fl = R.flattery && R.flattery.rehearsal, acc = F.accuracy, lab = R.lab && R.lab.rows.length ? R.lab.rows : null, base = lab && lab.find((r) => r.name === 'base');
  const O = R.round1 && R.round1.rehearsal, OX = R.round1 && R.round1.exam, calm = R.calm;
  const sh = (r) => (r.steady || r.full).sharpe, notes = (R.lab && R.lab.notes) || {};
  const verdictOf = (r) => (notes[r.name] ? esc(notes[r.name]) : R.lab.adopted === r.name ? '<b>running now</b>' : r === base ? 'where it started' : sh(r) > sh(base) + 0.1 ? 'better' : sh(r) < sh(base) - 0.1 ? 'worse' : 'about the same');
  const row = (n, a, b) => `<tr><td>${n}</td><td class="n">${pct(a.cagr, 1)}</td><td class="n">${pct(a.max_drawdown, 0)}</td>${b ? `<td class="n">${pct(b.cagr, 1)}</td><td class="n">${pct(b.max_drawdown, 0)}</td>` : ''}</tr>`;
  const more = (title, body, open = false) => `<details class="more" data-key="${esc(title)}" ${open ? 'open' : ''}><summary>${title}</summary><div>${body}</div></details>`;
  const modeRow = (k) => { const m = M[k], x = m.exam; return `<tr class="${k === key ? 'on' : ''}"><td><b>${MODE_COPY[k][0]}</b>${k === mine ? ' <span class="soft">(your setting)</span>' : ''}</td><td class="n">${pct(m.cagr, 1)}</td><td class="n">${pct(m.max_drawdown, 0)}</td><td class="n">${m.years_beat_spy} of ${m.years}</td>${x ? `<td class="n">${pct(x.cagr, 1)}</td><td class="n">${pct(x.max_drawdown, 0)}</td>` : ''}</tr>`; };
  const XS = M && M.exam_spy, AX = key && M[key].exam;
  const settings = M ? `<div class="card"><h2>The three risk settings, side by side</h2>
      <table class="tbl"><thead><tr><th>Setting</th><th class="n">A year, 2010 to 2024</th><th class="n">Worst fall</th><th class="n">Years it beat the market</th>${XS ? '<th class="n">A year, since 2025</th><th class="n">Worst fall</th>' : ''}</tr></thead><tbody>
      ${MODE_ORDER.filter((k) => M[k]).map(modeRow).join('')}
      <tr><td>S&amp;P 500 (the market)</td><td class="n">${pct(M.spy.cagr, 1)}</td><td class="n">${pct(M.spy.max_drawdown, 0)}</td><td class="n"></td>${XS ? `<td class="n">${pct(XS.cagr, 1)}</td><td class="n">${pct(XS.max_drawdown, 0)}</td>` : ''}</tr></tbody></table>
      <p class="muted" style="margin:10px 0 0">Same forecaster each time. Only how much cash it holds in rough markets changes. Pick one in <a data-nav="settings" tabindex="0" role="button">Settings</a>.${XS ? ' The forecaster was never tuned on the months since 2025. They were used once today, to reject the idea below.' : ''}</p></div>` : '';
  const K = R.candidate, BL = R.blend && R.blend.blend, same = !P.target || P.target === 'plain';
  const tried = K && M ? `<div class="card"><h2>The idea that looked better, and why it is not running</h2>
      <p>I tried ${lab ? lab.length : 'many'} ways to improve the forecaster. One stood out: teach it to prefer stocks that climb steadily. On 2010 to 2024 it beat the market with smaller falls. Then I ran it on the months since 2025, which were kept back for exactly this, and it fell flat.</p>
      <table class="tbl" style="max-width:820px"><thead><tr><th>Steady setting</th><th class="n">A year, 2010 to 2024</th><th class="n">A year, since 2025</th><th class="v">Verdict</th></tr></thead><tbody>
      <tr class="on"><td><b>The forecaster running now</b></td><td class="n">${pct(M.steady.cagr, 1)}</td><td class="n">${M.steady.exam ? pct(M.steady.exam.cagr, 1) : DASH}</td><td class="soft v">kept</td></tr>
      <tr><td>The "steady climbers" forecaster</td><td class="n">${pct(K.steady.cagr, 1)}</td><td class="n">${pct(K.steady.exam.cagr, 1)}</td><td class="soft v">rejected</td></tr>
      ${BL ? `<tr><td>Half of each</td><td class="n">${pct(BL.rehearsal.steady.cagr, 1)}</td><td class="n">${pct(BL.exam.steady.cagr, 1)}</td><td class="soft v">rejected</td></tr>` : ''}
      <tr><td>S&amp;P 500 (the market)</td><td class="n">${pct(M.spy.cagr, 1)}</td><td class="n">${M.exam_spy ? pct(M.exam_spy.cagr, 1) : DASH}</td><td></td></tr></tbody></table>
      <p class="muted" style="margin:10px 0 0">A model that only shines on the years it was chosen on is not an improvement, so the forecaster stays as it was. What did change today: the default risk setting (Cautious to ${MODE_COPY[M.default][0]}), a price-data bug, fresher decisions and these screens.</p></div>` : '';
  return `<div class="wrap"><h1 class="say">${head}</h1>
    ${what('A replay of 15 years of real prices with pretend money, after trading costs. Each year was traded by a model that had only seen earlier years. Not a promise about the future.')}
    <div class="cols2" style="margin-top:0"><div class="card"><h2>Nightdesk${name ? ` on ${name}` : ''} against simply owning the market</h2>
        <div class="cmphead">Average gain a year, 2010 to 2024</div><div class="cmp">${cmpRow('Nightdesk', A.cagr, maxR, C.s1, pct(A.cagr, 1).slice(1))}${cmpRow('S&amp;P 500', B.cagr, maxR, C.s2, pct(B.cagr, 1).slice(1))}</div>
        <div class="cmphead">Worst fall along the way (smaller is better)</div><div class="cmp">${cmpRow('Nightdesk', A.max_drawdown, maxD, C.s1, pct0(Math.abs(A.max_drawdown)))}${cmpRow('S&amp;P 500', B.max_drawdown, maxD, C.s2, pct0(Math.abs(B.max_drawdown)))}</div></div>
      <div class="card"><h2>What that means</h2>
        <p>${gap > 0.004 ? 'It finished ahead of the market in this test' : gap > -0.01 ? 'It roughly matched the market in this test' : 'It made less than the market in this test'}${calmer ? ', and its worst fall was smaller' : ''}.${A.years_beat_spy != null ? ` It beat the market in <b>${A.years_beat_spy} of ${A.years}</b> years, so expect losing years too.` : ''}</p>
        ${AX && XS ? `<p>Since 2025, months it was never tuned on: <b>${pct(AX.cagr, 1)}</b> a year against ${pct(XS.cagr, 1)} for the market.</p>` : ''}
        <p>${acc ? `Its picks beat the middle stock <b>${one(acc.week.picks_beat_average)}</b> of the time (a coin gets 50%). The edge is real, and thin.` : ''}</p>
        <p class="muted" style="margin:0">A test is not a promise. Treat the practice account as an experiment to watch.</p></div></div>
    ${settings}${tried}
    <h2>The detail, if you want it</h2>
    ${lab ? more('Everything I tried to make it better', `<p>Each change was tested the same fair way, on 2010 to 2024 only. "Ranking skill" is how well the scores lined up with what stocks did next (0 = none). The verdict compares gain for the risk taken on the Steady setting. Nothing here beat the forecaster already running once the months since 2025 were counted.</p>
      <table class="tbl lab"><thead><tr><th>Change</th><th class="n">Ranking skill</th><th class="n">Steady, a year</th><th class="n">Worst fall</th><th class="n">Fully invested, a year</th><th class="n">Worst fall</th><th>Verdict</th></tr></thead><tbody>
      ${lab.map((r) => { const st = r.steady || {}; return `<tr class="${R.lab.adopted === r.name ? 'on' : ''}"><td style="white-space:normal">${esc(r.label)}</td><td class="n">${num(r.ic_week, 3)}</td><td class="n">${st.cagr == null ? DASH : pct(st.cagr, 1)}</td><td class="n">${st.cagr == null ? DASH : pct(st.max_drawdown, 0)}</td><td class="n">${pct(r.full.cagr, 1)}</td><td class="n">${pct(r.full.max_drawdown, 0)}</td><td class="soft" style="white-space:normal;min-width:190px">${verdictOf(r)}</td></tr>`; }).join('')}</tbody></table>
      <p class="muted">One model per test here, to be quick. The most promising one was rebuilt with three models and run on the held-back months; see the card above.</p>`) : ''}
    ${calm && calm.calm && !same ? more('Is it just buying calm stocks?', `<p>Partly. A portfolio of simply the 50 least jumpy stocks, with no forecaster at all, made <b>${pct(calm.calm.steady.cagr, 1)}</b> a year on the Steady setting with a worst fall of ${pct0(Math.abs(calm.calm.steady.max_drawdown))}. The forecaster’s picks made <b>${pct((calm.model || M).steady.cagr, 1)}</b> with a worst fall of ${pct0(Math.abs((calm.model || M).steady.max_drawdown))}. So preferring calm stocks explains a good part of the result, and the forecaster adds the rest.</p>`) : ''}
    ${more('The fair test in full', `<p>The S&amp;P 500 membership was rebuilt day by day from the public record of who joined and left, with the price history of ${P.former_members_with_history || ''} dropped companies added back. A company counts only on days it was really in the index.${M ? ` Figures and charts here are for the ${MODE_COPY[M.default][0]} setting.` : ''}</p>
      <table class="tbl" style="max-width:900px"><thead><tr><th></th><th class="n">2010 to 2024, a year</th><th class="n">Worst fall</th>${X ? '<th class="n">Since 2025, a year</th><th class="n">Worst fall</th>' : ''}</tr></thead><tbody>
      ${row('Nightdesk', F.strategy, X && X.strategy)}${row('S&amp;P 500 (a real fund)', F.spy, X && X.spy)}${row('Every member equally, in this test', U, X && X.universe)}${row('A real fund that owns every member equally', E, X && X.real_equal_weight_fund)}</tbody></table>${fairCharts(F)}`)}
    ${D ? more('How the very first test fooled me', `<p>The first test used today’s list of 500 companies for every past year and said <b>${pct(D.strategy.cagr, 1)}</b> a year. But today’s list is made of winners: in 2012 that test could "buy" a small company that would only join the index in 2020. On the list as it really was, that same model made ${O ? pct(O.strategy.cagr, 1) : 'about 9%'}.</p>
      <p>What gave it away: the first test’s "own every stock equally" line beat a real fund that does exactly that by ${fl ? (fl.flattery * 100).toFixed(1) : 'about 5'} points a year.</p>`) : ''}
    ${F.luck ? more('Is the ranking adding anything at all?', `<p>Same rules, but with the scores dealt out to the stocks at random, 30 times. Random scores made between ${pct(F.luck.shuffled_low, 1)} and ${pct(F.luck.shuffled_high, 1)} a year. The real scores made ${pct(F.luck.actual.cagr, 1)}, better than ${all(F.luck)}.</p>`) : ''}
    ${more('What trading costs do to it', `<table class="tbl" style="max-width:620px"><thead><tr><th>If each trade cost</th><th class="n">A year</th><th class="n">Worst fall</th></tr></thead><tbody>
      ${F.cost_sensitivity.map((r) => `<tr><td>${(r.cost_bps / 100).toFixed(2)}%${r.cost_bps === 5 ? ' (the test setting)' : ''}</td><td class="n">${pct(r.cagr, 1)}</td><td class="n">${pct(r.max_drawdown, 0)}</td></tr>`).join('')}</tbody></table>`)}
    ${R.intraday ? more('Why it doesn’t trade faster', `<p>A version trading every 30 minutes could see a faint pattern (${num(R.intraday.top_edge_bps / 100, 3)}% per trade) but each round trip costs ${num(R.intraday.cost_cheap_bps / 100, 2)}% to ${num(R.intraday.cost_realistic_bps / 100, 2)}%. It would have lost money.</p>`) : ''}</div>`;
}

// ---------- Activity ----------
function activityView() {
  const tabs = `<div class="tabs" role="tablist"><button role="tab" data-atab="did" aria-selected="${S.atab === 'did'}">What it did</button><button role="tab" data-atab="news" aria-selected="${S.atab === 'news'}">Headlines it read</button></div>`;
  if (S.atab === 'news') return `<div class="wrap"><h1 class="say">Headlines, read by your own model.</h1>${tabs}${newsBody()}</div>`;
  const kinds = [['main', 'Everything that matters'], ['trade', 'Trades'], ['decision', 'Decisions'], ['risk', 'Safety'], ['user', 'Your changes'], ['system', 'Engine housekeeping']];
  const items = S.activity.filter((a) => (S.actFilter === 'main' ? a.kind !== 'system' : a.kind === S.actFilter));
  return `<div class="wrap"><h1 class="say">Everything it did, and why.</h1>${what('Newest first, in your local time. Every trade says why it was made.')}${tabs}
    <div class="row" style="margin-bottom:4px"><div class="seg">${kinds.map(([k, l]) => `<button data-af="${k}" aria-pressed="${S.actFilter === k}">${l}</button>`).join('')}</div>
      <button class="btn" data-act="export">Save trades as a spreadsheet</button></div><div style="max-width:940px">${feedList(items, true)}</div></div>`;
}
function newsBody() {
  const n = S.news; if (!n) return `<p class="empty">Loading.</p>`;
  const r = n.reader, sb = n.scoreboard, st = r.stats || {}, dot = { good: 'var(--gain)', bad: 'var(--loss)', nothing: 'var(--paper-3)' };
  const state = !r.available ? pill('bad', 'Optional reader not configured') : r.status === 'error' ? pill('bad', 'Reader had a problem') : r.awake ? pill('live', 'Reader awake') : pill('', 'Reader asleep (using no memory)');
  const verdict = !sb.blocks ? 'It has not flagged a purchase yet.' : !sb.judged ? `It has flagged ${sb.blocks} ${sb.blocks === 1 ? 'purchase' : 'purchases'}. Too early to judge.`
    : `Of ${sb.judged} flagged purchases, ${sb.helped} went on to do worse than the market, so blocking them would have helped.${sb.judged < 30 ? ' Far too few to mean anything yet.' : ''}`;
  return `${what('Your optional compatible reader labels fresh headlines about stocks Nightdesk owns or is about to buy: good, bad or nothing. It is on trial: it flags and keeps score, but cannot trade or block anything unless you switch that on in Settings.')}
    <div class="row" style="margin-bottom:12px">${state}${pill('', n.veto_on ? 'Blocking purchases: on' : 'Blocking purchases: off (on trial)')}${n.last_refresh ? `<span class="muted">Last checked ${tLocal(n.last_refresh)}.</span>` : ''}</div>
    <div class="cols2" style="margin-top:0"><div class="card"><h2>Scoreboard</h2><p style="margin:0">${esc(verdict)}</p></div>
      <div class="card"><h2>What it uses on this computer</h2><div class="kv"><span>Memory while awake</span><span>${st.footprint_mb ? (st.footprint_mb / 1000).toFixed(1) + ' GB' : 'not measured yet'}</span><span>Memory while asleep</span><span>none</span>
        <span>Time to wake</span><span>${st.load_s ? st.load_s.toFixed(1) + ' s' : 'not measured yet'}</span><span>Time per headline</span><span>${st.read_ms && st.headlines ? Math.max(1, Math.round(st.read_ms / st.headlines)) + ' thousandths of a second' : 'not measured yet'}</span></div></div></div>
    <h2>What it has read</h2>
    ${n.items.length ? `<table class="tbl"><thead><tr><th style="width:118px">When</th><th style="width:70px">Stock</th><th>Headline</th><th style="width:130px">Its reading</th><th style="width:120px">Counts?</th></tr></thead><tbody>
      ${n.items.map((i) => `<tr data-key="${esc(i.symbol + i.title)}"><td class="muted" style="white-space:nowrap">${tLocal(i.ts, { weekday: 'short' })}</td><td class="sym">${esc(i.symbol)}</td>
        <td class="sel" style="white-space:normal">${esc(i.title)}<small>${esc(i.publisher)}</small></td>
        <td><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dot[i.label]};margin-right:7px"></i>${i.label === 'nothing' ? 'Nothing' : `${i.label === 'bad' ? 'Bad' : 'Good'}, ${Math.round(i.conf * 100)}% sure`}</td>
        <td class="soft">${i.specific ? 'Yes' : 'No<small>general or a price recap</small>'}</td></tr>`).join('')}</tbody></table>`
      : `<p class="empty">No headlines yet. The reader checks every 15 minutes while New York is open.</p>`}`;
}

// ---------- Settings ----------
const MODE_COPY = {
  cautious: ['Cautious', 'Holds cash when markets are stormy, and halves the bet in a falling market.'],
  steady: ['Steady', 'Stays invested, and only brakes in a real storm.'],
  full: ['Fully invested', 'Always fully in stocks. Biggest gains in good years, biggest falls in bad ones.'] };
const MODE_ORDER = ['cautious', 'steady', 'full'];
const modeOf = (st) => { const M = S.modes || {}; return MODE_ORDER.find((k) => M[k] && Math.abs(st.target_vol - M[k].target_vol) < 0.005 && st.trend_filter === M[k].trend_filter) || 'custom'; };
const ADVANCED = [
  ['max_positions', 'int', 'How many stocks to hold', 'Tested at 50. Fewer is bumpier; more behaves like the index.', 'stocks'],
  ['max_weight', 'pct', 'Biggest single holding', 'No stock may be more than this share of the account.', '%'],
  ['max_sector_weight', 'pct', 'Biggest single industry', 'So it can’t quietly become one big bet on, say, tech.', '%'],
  ['slippage_bps', 'num', 'Pretend cost of each trade', 'Charged on every buy and sell so results aren’t flattered. 3 means 0.03%.', 'hundredths of 1%'],
  ['rebalance_minutes_before_close', 'int', 'Daily check, this long before New York closes', 'When it compares what it owns with the rankings.', 'minutes'],
  ['keep_awake', 'bool', 'Keep this computer awake while the engine runs (macOS)', 'The screen can still switch off.'],
  ['news_veto', 'bool', 'Let your headline reader block purchases', 'Off because it is unproven. While off it still reads and keeps score.']];
const SAFETY = [
  ['position_stop', 'pct', 'Sell a stock that falls this far below what was paid', 'It is then left alone for 5 days.', '%'],
  ['daily_loss_limit', 'pct', 'Stop buying for the day after losing', 'Holdings are kept. Buying resumes next session.', '%'],
  ['max_drawdown_limit', 'pct', 'Safety switch: sell everything after falling this far from the high', 'Trading then stays off until you switch it back on.', '%']];
function settingsView() {
  const st = S.settings, s = S.snap; if (!st) return `<div class="wrap"><h1 class="say">Settings</h1><p class="sub">Loading.</p></div>`;
  const field = ([key, type, label, help, unit]) => { const v = st[key];
    const control = type === 'bool' ? `<input type="checkbox" class="switch" id="f-${key}" data-set="${key}" data-type="bool" ${v ? 'checked' : ''}>`
      : `<span><input type="number" id="f-${key}" data-set="${key}" data-type="${type}" step="${type === 'num' ? 0.5 : 1}" value="${type === 'pct' ? +(v * 100).toFixed(2) : v}"><span class="unit">${esc(unit || '')}</span></span>`;
    return `<div class="set" data-key="${key}"><label for="f-${key}">${esc(label)}<small>${esc(help)}</small></label>${control}</div>`; };
  const c = S.confirm, f = s ? s.feed : {}, e = s ? s.engine : {}, mode = modeOf(st), M = (S.research && S.research.modes) || {};
  const fact = (k) => (M[k] ? `In the fair test: about ${pct(M[k].cagr, 0).slice(1)} a year, worst fall ${pct0(Math.abs(M[k].max_drawdown))}. Beat the market in ${M[k].years_beat_spy} of ${M[k].years} years.` : '');
  const spyLine = M.spy ? `Simply owning the S&P 500 made about ${pct(M.spy.cagr, 0).slice(1)} a year in the same test, with a worst fall of ${pct0(Math.abs(M.spy.max_drawdown))}.` : '';
  return `<div class="wrap"><h1 class="say">Settings</h1>${what('Changes save as you make them and apply from the next decision. It is all pretend money, so nothing here can cost you anything real.')}
    ${setupPanel()}
    <div class="set"><label for="f-auto_trade">Trade automatically<small>When off, it keeps watching and ranking but places no orders.</small></label><input type="checkbox" class="switch" id="f-auto_trade" data-set="auto_trade" data-type="bool" ${st.auto_trade ? 'checked' : ''}></div>
    <details class="more" data-key="reader-setup"><summary>Optional headline reader</summary><div>
      <p>Use your own locally trained classifier with labels 0=good, 1=bad, 2=nothing. Its config.json must declare those labels and its weights must be model.safetensors. Generic positive/negative/neutral models use different semantics and are incompatible. The forecaster works without this reader.</p>
      <div class="set"><label for="reader-python">Python executable<small>Your Python environment must contain PyTorch and Transformers.</small></label><input id="reader-python" type="text" data-reader-input="python" value="${esc(S.readerPython)}" placeholder="Path to your Python executable"></div>
      <div class="set"><label for="reader-model">Model directory<small>Local files only; no weights or remote code are downloaded.</small></label><input id="reader-model" type="text" data-reader-input="model" value="${esc(S.readerModel)}" placeholder="Path to your own model directory"></div>
      <button class="btn" data-act="reader-save">Save reader configuration</button> <button class="btn" data-act="reader-clear">Disable reader</button>
    </div></details>
    <h2>How much risk to take</h2>${what('The one choice that matters most. ' + spyLine)}
    <div class="modes">${MODE_ORDER.filter((k) => S.modes && S.modes[k]).map((k) => `<button class="mode" data-mode="${k}" aria-pressed="${mode === k}"><b>${MODE_COPY[k][0]}${k === S.defaultMode ? '<i class="tag">Default</i>' : ''}</b><span>${MODE_COPY[k][1]}</span><em>${fact(k)}</em><small>Safety switch at ${pct0(S.modes[k].max_drawdown_limit)}</small></button>`).join('')}</div>
    ${mode === 'custom' ? `<p class="muted">You are on custom risk settings (aiming for ${pct0(st.target_vol)} yearly swings, downtrend rule ${st.trend_filter ? 'on' : 'off'}). Pick a card above to go back to a tested one.</p>` : ''}
    <h2>Safety limits</h2>${SAFETY.map(field).join('')}
    <details class="more" data-key="adv" style="margin-top:24px"><summary>Advanced</summary><div>${ADVANCED.map(field).join('')}
      <div class="set"><label>Put everything back to the tested settings<small>Undoes every change on this page.</small></label><button class="btn" data-act="defaults">Reset settings</button></div></div></details>
    <details class="more" data-key="acct"><summary>The practice account</summary><div>
      <div class="set"><label>Sell everything now<small>Sells every holding at the next price and pauses trading. Only works while New York is open.</small></label>
        <button class="btn danger" data-act="sell_all">${c === 'sell_all' ? 'Click again to confirm' : 'Sell everything'}</button></div>
      <div class="set"><label for="f-reset">Start again from scratch<small>Wipes the holdings, the history and the activity log, and starts over with this much pretend money.</small></label>
        <span><input type="number" id="f-reset" step="1000" data-reset value="${S.resetAmount ?? st.starting_cash}"><span class="unit">dollars</span></span></div>
      <div class="set"><label></label><button class="btn danger" data-act="reset">${c === 'reset' ? 'Click again to wipe and restart' : 'Wipe and start again'}</button></div></div></details>
    <details class="more" data-key="bonnet"><summary>Under the bonnet</summary><div><div class="kv" style="max-width:720px">
      <span>Engine</span><span>${e.ready ? 'Running for ' + dur(e.uptime) : esc(e.message || 'Not answering')}</span>
      <span>Price lines connected</span><span>${f.lines_up ?? 0} of ${f.lines ?? 0}</span><span>Price updates received</span><span>${(f.messages || 0).toLocaleString('en-AU')}</span>
      <span>Engine memory</span><span>${e.memory_mb ? (e.memory_mb / 1000).toFixed(1) + ' GB' : DASH}</span>
      <span>Last self-check of the price feed</span><span>${e.self_check ? `${e.self_check.top20_same} of the top 20 matched on ${longDate(e.self_check.day)}` : 'Runs after each closing bell'}</span>
      <span>Forecaster trained on prices up to</span><span>${e.model && e.model.trained_through ? longDate(e.model.trained_through) : DASH}</span></div>
      <p style="margin-top:12px"><button class="btn" data-open="log">Show the engine log</button> <button class="btn" data-open="folder">Show your user data folder</button></p></div></details>
    <p class="note warn" style="margin-top:22px"><b>Real money is not connected.</b> There is no broker login anywhere in this app, on purpose.</p></div>`;
}

// ---------- How it works ----------
function helpView() {
  const s = S.snap, R = S.research || {}, f = s ? s.feed : {}, l = s ? s.latency : {}, e = s ? s.engine : {}, meta = e.model || {}, st = S.settings || {};
  const ms = (v) => (v == null ? 'not measured yet' : v >= 1000 ? (v / 1000).toFixed(1) + ' seconds' : Math.round(v) + ' thousandths of a second');
  const rr = S.news && S.news.reader, n = st.max_positions || 50, live = {};
  return `<div class="wrap"><h1 class="say">How it works.</h1>${what('Five steps, repeated every trading day. It ranks stocks against each other. It does not try to guess prices.')}
    <div class="flow">
      <div><b>Prices come in</b><span>${f.symbols || 505} stocks on ${f.lines || 6} live lines from Yahoo Finance${s && s.clock.phase === 'open' && f.rate ? `, about ${Math.round(f.rate)} price updates a second right now` : ''}. Plus 20 years of daily history.</span></div>
      <div><b>It measures each stock</b><span>${meta.signals || 65} measurements per stock: how the price has moved, how jumpy it is, how busy the trading is${meta.signals > 66 ? ', and how its last profit report went' : ''}.</span></div>
      <div><b>A committee scores each stock</b><span>${meta.members || 8} small models vote on which stocks will do better than the rest over the next few weeks${meta.target && meta.target !== 'plain' ? ', for the risk they carry' : ''}. ${meta.rows_h5 ? 'Trained on ' + (meta.rows_h5 / 1e6).toFixed(1) + ' million past examples.' : 'Training uses your locally downloaded history.'}</span></div>
      <div><b>The best ${n} become the portfolio</b><span>Equal slices. A stock stays until it drops out of the top ${Math.round(n * (st.hold_buffer || 4))}. No more than ${Math.floor(n * (st.max_sector_weight || 0.3))} from one industry.</span></div>
      <div><b>It trades once a day</b><span>About ${st.rebalance_minutes_before_close || 15} minutes before New York closes, a practice broker buys and sells at the next real price, with a small cost on every trade. Pretend money only.</span></div>
    </div>
    <div class="cols2"><div class="card"><h2>Speed, measured on this computer</h2><div class="kv">
        <span>A price reaching this computer after it happens in New York</span><span>${ms(f.median_ms ?? live.median_ms)}</span><span>Re-ranking ${e.board_size || 0} stocks</span><span>${ms(l.decision_ms ?? live.decision_ms)}</span>
        <span>An order waiting for its next price</span><span>${ms(l.fill_ms ?? live.fill_ms)}</span><span>How often it re-ranks while the market is open</span><span>every ${e.score_every || 10} seconds, and again just before it decides</span><span>Reacting to a stop-loss price</span><span>${l.stop_us == null ? 'not measured yet' : Math.round(l.stop_us) + ' millionths of a second'}</span></div>
        <p class="muted" style="margin:10px 0 0">${f.measured_on || live.day ? `Measured on ${longDate(f.measured_on || live.day)}. ` : ''}Free price feeds can be delayed or unavailable. These numbers come from your own session when available.</p></div>
      <div class="card"><h2>The AI on your computer</h2><div class="kv"><span>Your optional headline reader</span><span>on trial</span><span>Its memory while awake</span><span>${rr && rr.stats && rr.stats.footprint_mb ? (rr.stats.footprint_mb / 1000).toFixed(1) + ' GB' : 'not measured yet'}</span>
        <span>Its memory while asleep</span><span>none</span><span>The forecaster (trees, not a chatbot)</span><span>${e.memory_mb ? (e.memory_mb / 1000).toFixed(1) + ' GB with the engine' : 'not measured yet'}</span></div></div></div>
    <h2>Words used in this app</h2>
    <table class="tbl" style="max-width:860px"><tbody>
      <tr><td style="width:190px"><b>Practice account</b></td><td class="soft">Pretend money traded at real prices, to see what would have happened.</td></tr>
      <tr><td><b>The market / S&amp;P 500</b></td><td class="soft">America’s 500 biggest companies. The simple thing to compare against.</td></tr>
      <tr><td><b>Rank and score</b></td><td class="soft">Where the forecaster places a stock against the others. Not a price target.</td></tr>
      <tr><td><b>Invested</b></td><td class="soft">The share of the account in stocks. The rest sits in cash.</td></tr>
      <tr><td><b>Worst fall</b></td><td class="soft">The biggest drop from a high point to the next low point.</td></tr>
      <tr><td><b>Fair test</b></td><td class="soft">A replay of history that only lets the strategy buy what it could really have bought at the time.</td></tr></tbody></table>
    <p style="margin-top:18px"><button class="btn primary" data-open="explainer">Open the full explainer page</button></p></div>`;
}

// ---------- render loop ----------
const views = { home: homeView, portfolio: portfolioView, results: resultsView, activity: activityView, settings: settingsView, help: helpView };
let queued = false;
function draw() { if (!queued) return; queued = false;
  morph($('#top'), topBar()); morph($('#nav'), nav()); morph($('#view'), (views[S.view] || homeView)());
  const d = $('#drawer'); d.hidden = !S.detail; if (S.detail) morph(d, drawer()); }
// Draw on the next frame. A hidden window gets no frames, so a timer is the fallback.
function render() { if (queued) return; queued = true; requestAnimationFrame(draw); setTimeout(draw, 80); }

// ---------- data ----------
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => { S.snap = JSON.parse(ev.data); S.offline = false; render(); };
  ws.onclose = () => { S.offline = true; render(); setTimeout(connect, 2000); };
  ws.onerror = () => ws.close();
}
const quiet = (p) => p.catch(() => {});
const load = {
  activity: () => quiet(api('/api/activity?limit=400').then((a) => { S.activity = a; render(); })),
  curve: () => quiet(api('/api/curve?span=' + S.span).then((c) => { S.curve = c; render(); })),
  board: () => quiet(api('/api/board').then((b) => { S.board = b; render(); })),
  research: () => quiet(api('/api/research').then((r) => { S.research = r; render(); })),
  news: () => quiet(api('/api/news').then((n) => { S.news = n; render(); })),
  settings: () => quiet(api('/api/settings').then((r) => { S.settings = r.settings; if (!document.activeElement?.matches('[data-reader-input]')) { S.readerPython = r.settings.reader_python || ''; S.readerModel = r.settings.reader_model || ''; } S.limits = r.limits; S.modes = r.modes; S.defaultMode = r.default_mode; render(); })),
};
function go(view) { S.view = view; localStorage.setItem('nd.view', view); S.confirm = null; $('#view').scrollTop = 0; render();
  if (view === 'portfolio') load.board(); if (view === 'activity') load.news(); if (view === 'results' || view === 'help' || view === 'settings') load.research(); if (view === 'settings') load.settings(); }

async function act(name) {
  if (name === 'setup' || name === 'disconnect') {
    try { await api('/api/setup', name === 'setup' ? { network_enabled: true, start: true } : { network_enabled: false }); toast(name === 'setup' ? 'Setup started. Practice trading stays paused.' : 'Offline. Practice trading paused.'); load.settings(); }
    catch (err) { toast(String(err.message || err)); }
    return render();
  }
  if (name === 'reader-save' || name === 'reader-clear') {
    try { const body = name === 'reader-clear' ? { python: '', model: '' } : { python: S.readerPython, model: S.readerModel };
      await api('/api/reader', body); S.readerPython = body.python; S.readerModel = body.model; toast(name === 'reader-clear' ? 'Reader disabled.' : 'Reader configured.'); load.news(); }
    catch (err) { toast(String(err.message || err)); } return render();
  }
  if (name === 'showall') { S.showAll = true; return render(); }
  if (name === 'guide') { S.guide = false; localStorage.setItem('nd.guide.done', '1'); return render(); }
  if (name === 'export') { try { const r = await api('/api/export', { reveal: true }); toast(`Saved ${r.rows} trades. Saved in your user data exports folder.`); } catch (err) { toast(String(err.message || err)); } return; }
  if (name === 'defaults') { try { const r = await api('/api/settings/defaults', {}); S.settings = r.settings; toast('Back to the tested settings.'); } catch (err) { toast(String(err.message || err)); } return render(); }
  if ((name === 'sell_all' || name === 'reset') && S.confirm !== name) { S.confirm = name; render(); setTimeout(() => { if (S.confirm === name) { S.confirm = null; render(); } }, 5000); return; }
  S.confirm = null;
  try { const body = { action: name }; if (name === 'reset') { body.value = +(S.resetAmount ?? (S.settings || {}).starting_cash) || 100000; S.resetAmount = null; }
    const r = await api('/api/control', body);
    if (!r.ok) { toast(r.message || 'The action could not be completed.'); return render(); }
    toast({ pause: 'Paused.', resume: 'Trading resumed.', rebalance: 'Checking the portfolio now.', sell_all: 'Selling everything.', reset: 'Practice account reset.' }[name] || 'Done.');
    load.activity(); load.settings(); if (name === 'reset') { S.curve = null; load.curve(); }
  } catch (err) { toast(String(err.message || err)); }
  render();
}
async function saveSetting(el) {
  const key = el.dataset.set, type = el.dataset.type; let v = type === 'bool' ? el.checked : parseFloat(el.value);
  if (type !== 'bool' && !isFinite(v)) return toast('That needs a number.');
  if (type === 'pct') v = v / 100; if (type === 'int') v = Math.round(v);
  const before = S.settings[key]; S.settings[key] = v;                                   // show the new value at once; put it back if the engine refuses
  try { const r = await api('/api/settings', { [key]: v }); S.settings = r.settings; toast(r.changed.length ? 'Saved.' : 'No change.'); }
  catch (err) { S.settings[key] = before; el.value = type === 'pct' ? +(before * 100).toFixed(2) : before; toast(String(err.message || err)); load.settings(); }
  render();
}
async function setMode(k) { const m = S.modes && S.modes[k]; if (!m) return;
  try { const r = await api('/api/settings', m); S.settings = r.settings; toast(r.changed.length ? `Risk set to: ${MODE_COPY[k][0]}.` : 'Already on that setting.'); } catch (err) { toast(String(err.message || err)); } render(); }

document.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-nav],[data-act],[data-sym],[data-span],[data-cspan],[data-af],[data-close],[data-ptab],[data-psort],[data-atab],[data-mode],[data-open]'); if (!t) return;
  if (t.dataset.nav) go(t.dataset.nav);
  else if (t.dataset.act) act(t.dataset.act);
  else if (t.dataset.span) openDetail(S.detail.symbol, t.dataset.span);
  else if (t.dataset.cspan) { S.span = t.dataset.cspan; load.curve(); render(); }
  else if (t.dataset.af) { S.actFilter = t.dataset.af; render(); }
  else if (t.dataset.ptab) { S.ptab = t.dataset.ptab; if (S.ptab === 'all') load.board(); render(); }
  else if (t.dataset.psort) { S.psort = t.dataset.psort; render(); }
  else if (t.dataset.atab) { S.atab = t.dataset.atab; if (S.atab === 'news') load.news(); render(); }
  else if (t.dataset.mode) setMode(t.dataset.mode);
  else if (t.dataset.open) quiet(api('/api/open', { what: t.dataset.open }));
  else if (t.hasAttribute('data-close')) { S.detail = null; render(); }
  else if (t.dataset.sym) { if (!S.board) load.board(); openDetail(t.dataset.sym); }
});
document.addEventListener('input', (ev) => { if (ev.target.matches('[data-reader-input]')) { if (ev.target.dataset.readerInput === 'python') S.readerPython = ev.target.value; else S.readerModel = ev.target.value; }
  else if (ev.target.matches('[data-search]')) { S.filter = ev.target.value; render(); }
  else if (ev.target.matches('[data-reset]')) S.resetAmount = ev.target.value; });      // remember what was typed: a redraw must never wipe it
document.addEventListener('change', (ev) => { if (ev.target.matches('[data-set]')) saveSetting(ev.target); });
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && S.detail) { S.detail = null; render(); }
  else if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches && ev.target.matches('[data-sym]')) { ev.preventDefault(); if (!S.board) load.board(); openDetail(ev.target.dataset.sym); } });

wireCharts(document.body);
connect(); render();
load.activity(); load.curve(); load.settings(); load.research(); load.board();
setInterval(load.activity, 4000);
setInterval(() => { if (S.view === 'home') load.curve(); }, 15000);
setInterval(() => { if (S.view === 'portfolio' || S.detail) load.board(); }, 15000);
setInterval(() => { if (S.view === 'results' || S.view === 'help') load.research(); }, 120000);
setInterval(() => { if (S.view === 'activity' && S.atab === 'news') load.news(); }, 30000);
setInterval(render, 1000);
