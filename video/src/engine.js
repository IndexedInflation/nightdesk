// The stage engine. Everything on screen is a pure function of the time t (seconds), so any frame
// can be drawn on its own, in any order. No CSS transitions, no timers: renderAt(t) sets every style.
//
// Markup conventions (resolved once at boot):
//   data-at="day.3+0.4"      when the element arrives ("day.3" = start of that beat, "day.3e" = its end, +/- seconds)
//   data-fx="up|down|left|right|pop|fade|zoom|wipe|growx|growy|draw|count|drop"
//   data-dur="0.6"           how long the arrival takes
//   data-out="day.5"         when it leaves (fades), optional
//   data-on="idea.12"        adds class "on" from that moment (data-off removes it again)
//   data-pulse="a.1,a.2"     a quick bump at those moments
//   data-count="0,257,$"     with fx="count": from, to, prefix (and data-suffix, data-dec)
(function () {
  const D = window.DATA, TL = D.timeline, BEATS = TL.beats;
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const ease = { out: (p) => 1 - Math.pow(1 - p, 3), inout: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    back: (p) => { const c = 1.70158; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); }, lin: (p) => p };
  const T = (ref) => {
    if (typeof ref === 'number') return ref;
    const m = /^([a-z]+\.\d+)(?:#(\d+))?(e)?((?:[+-][\d.]+)*)$/.exec(String(ref).trim());
    if (!m || !BEATS[m[1]]) throw new Error('unknown time: ' + ref);
    const b = BEATS[m[1]];
    let base;
    if (m[2] != null) {                                  // "day.3#1": a caption chunk of that beat
      const c = (b.chunks || [])[+m[2]];
      if (!c) throw new Error('unknown chunk: ' + ref);
      base = m[3] ? c.end : c.start;
    } else base = m[3] ? b.end : b.start;
    return base + (m[4] ? m[4].match(/[+-][\d.]+/g).reduce((s, x) => s + +x, 0) : 0);   // several offsets add up: "a.1#2+0.9+0.7"
  };
  const scenes = [];           // {id, el, start, end, items: [...], update}
  const custom = {};           // scene id -> function(t, local, scene)

  const FX = {
    up: (el, p) => { el.style.opacity = p; el.style.transform = `translateY(${(1 - p) * 44}px)`; },
    down: (el, p) => { el.style.opacity = p; el.style.transform = `translateY(${(p - 1) * 44}px)`; },
    left: (el, p) => { el.style.opacity = p; el.style.transform = `translateX(${(1 - p) * 70}px)`; },
    right: (el, p) => { el.style.opacity = p; el.style.transform = `translateX(${(p - 1) * 70}px)`; },
    fade: (el, p) => { el.style.opacity = p; },
    zoom: (el, p) => { el.style.opacity = p; el.style.transform = `scale(${1.18 - 0.18 * p})`; },
    pop: (el, p, raw) => { el.style.opacity = clamp(raw * 3); el.style.transform = `scale(${0.4 + 0.6 * ease.back(raw)})`; },
    drop: (el, p, raw) => { el.style.opacity = clamp(raw * 3); el.style.transform = `translateY(${(1 - ease.back(raw)) * -90}px) rotate(${(1 - p) * -8}deg)`; },
    wipe: (el, p) => { el.style.opacity = 1; el.style.clipPath = `inset(0 ${(1 - p) * 100}% 0 0)`; },
    growx: (el, p) => { el.style.transform = `scaleX(${p})`; },
    growy: (el, p) => { el.style.transform = `scaleY(${p})`; },
    draw: (el, p, raw, it) => { el.style.strokeDasharray = it.len; el.style.strokeDashoffset = it.len * (1 - p); el.style.opacity = raw > 0 ? 1 : 0; },
    count: (el, p, raw, it) => {
      const v = it.from + (it.to - it.from) * p, dec = it.dec;
      const txt = Math.abs(v).toLocaleString('en-AU', { minimumFractionDigits: dec, maximumFractionDigits: dec });
      el.textContent = (it.sign && v > 0 ? '+' : v < 0 ? '−' : '') + it.prefix + txt + it.suffix; el.style.opacity = clamp(raw * 4);
    },
  };

  function collect(scene) {
    scene.items = [];
    scene.el.querySelectorAll('[data-at],[data-on],[data-pulse]').forEach((el) => {
      const d = el.dataset, it = { el, fx: d.fx || 'up', dur: +(d.dur || 0.55) };
      it.at = d.at != null ? T(d.at) : null;
      it.out = d.out != null ? T(d.out) : null;
      it.on = d.on != null ? T(d.on) : null;
      it.off = d.off != null ? T(d.off) : null;
      it.pulse = d.pulse ? d.pulse.split(',').map(T) : null;
      it.easing = ease[d.ease || 'out'];
      if (it.fx === 'draw') it.len = el.getTotalLength ? el.getTotalLength() : 1000;
      if (it.fx === 'count') { const [a, b, pre] = (d.count || '0,0,').split(','); it.from = +a; it.to = +b; it.prefix = pre || ''; it.suffix = d.suffix || ''; it.dec = +(d.dec || 0); it.sign = d.sign === '1'; }
      scene.items.push(it);
    });
  }

  function applyItem(it, t) {
    const el = it.el;
    if (it.at != null) {
      const raw = clamp((t - it.at) / it.dur), p = it.easing(raw);
      (FX[it.fx] || FX.up)(el, p, raw, it);
      if (it.out != null && t >= it.out) el.style.opacity = String(Math.min(+el.style.opacity || 0, 1 - clamp((t - it.out) / 0.35)));
      el.style.visibility = (raw <= 0 && it.fx !== 'growx' && it.fx !== 'growy') || (it.out != null && t >= it.out + 0.35) ? 'hidden' : 'visible';
    }
    if (it.on != null) el.classList.toggle('on', t >= it.on && (it.off == null || t < it.off));
    if (it.pulse) {
      let s = 1;
      for (const pt of it.pulse) { const q = (t - pt) / 0.5; if (q > 0 && q < 1) s = 1 + 0.12 * Math.sin(q * Math.PI); }
      if (it.at == null) el.style.transform = `scale(${s})`; else if (s !== 1) el.style.transform = (el.style.transform || '') + ` scale(${s})`;
    }
  }

  // ---- sky: slow stars, so even a still moment has a little life
  const stars = Array.from({ length: 90 }, (_, i) => { const r = (n) => { const x = Math.sin(i * 127.1 + n * 311.7) * 43758.5453; return x - Math.floor(x); }; return { x: r(1) * 1920, y: r(2) * 760, s: 0.6 + r(3) * 1.6, ph: r(4) * 6.28, sp: 0.4 + r(5) * 1.2 }; });
  function sky(t, glow) {
    const c = document.getElementById('sky').getContext('2d');
    const g = c.createLinearGradient(0, 0, 0, 1080); g.addColorStop(0, '#080d18'); g.addColorStop(0.62, '#0d1526'); g.addColorStop(1, '#121b30');
    c.fillStyle = g; c.fillRect(0, 0, 1920, 1080);
    const rg = c.createRadialGradient(960, 1180, 60, 960, 1180, 900); rg.addColorStop(0, `rgba(242,178,76,${0.10 + 0.05 * glow})`); rg.addColorStop(1, 'rgba(242,178,76,0)');
    c.fillStyle = rg; c.fillRect(0, 0, 1920, 1080);
    for (const s of stars) { const a = 0.18 + 0.22 * (0.5 + 0.5 * Math.sin(t * s.sp + s.ph)); c.fillStyle = `rgba(236,232,221,${a})`; c.beginPath(); c.arc((s.x + t * 2.2 * s.s) % 1920, s.y, s.s, 0, 6.283); c.fill(); }
  }

  const fmtLeft = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const starHtml = (text) => { const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;'); let open = false; return esc.replace(/\*/g, () => ((open = !open) ? '<b>' : '</b>')) + (open ? '</b>' : ''); };

  function hud(t) {
    const sc = TL.scenes.find((s) => t >= s.start && t < s.end) || TL.scenes[TL.scenes.length - 1];
    const p = clamp(t / TL.duration);
    document.getElementById('fill').style.width = (p * 100).toFixed(3) + '%';
    document.getElementById('knob').style.left = (p * 100).toFixed(3) + '%';
    document.getElementById('left').textContent = fmtLeft(TL.duration - t) + ' left';
    const total = TL.scenes.filter((s) => s.chapter >= 1 && s.chapter <= 10).length;
    document.getElementById('chapter').innerHTML = sc.chapter >= 1 && sc.chapter <= total ? `<b>${sc.chapter}</b><i>/ ${total}</i><span>${sc.title}</span>` : sc.chapter === 0 ? '<span>Start here</span>' : '<span>Wrap-up</span>';
    document.querySelectorAll('#segs i').forEach((el, i) => el.classList.toggle('now', TL.scenes[i] === sc));
    // caption: the chunk being spoken, or nothing in a silence
    let text = '';
    for (const id in BEATS) { const b = BEATS[id], caps = b.caps || b.chunks; if (caps && t >= b.start - 0.05 && t < b.end + 0.18) { const ch = caps.find((c) => t < c.end) || caps[caps.length - 1]; text = ch.text; break; } }
    const cap = document.getElementById('captext');
    if (cap.dataset.raw !== text) { cap.dataset.raw = text; cap.innerHTML = starHtml(text); }
    document.getElementById('caption').style.opacity = text ? 1 : 0;
    const env = TL.env[Math.min(TL.env.length - 1, Math.max(0, Math.round(t * TL.fps)))] || 0;
    const lamp = document.getElementById('lamp'); lamp.style.boxShadow = `0 0 0 ${4 + env * 7}px rgba(242,178,76,${0.14 + env * 0.2}), 0 0 ${18 + env * 34}px ${4 + env * 10}px rgba(242,178,76,${0.3 + env * 0.4})`;
    return env;
  }

  function renderAt(t) {
    t = clamp(t, 0, TL.duration);
    const env = hud(t);
    sky(t, env);
    for (const s of scenes) {
      const live = t >= s.start - 0.001 && t < s.end + 0.001;
      s.el.style.display = live ? 'block' : 'none';
      if (!live) continue;
      const fin = ease.out(clamp((t - s.start) / 0.5)), fout = clamp((s.end - t) / 0.3);
      s.el.style.opacity = Math.min(fin, fout);
      s.el.style.transform = `scale(${0.985 + 0.015 * fin})`;
      for (const it of s.items) applyItem(it, t);
      if (custom[s.id]) custom[s.id](t, t - s.start, s);
    }
    return t;
  }

  function boot() {
    const root = document.getElementById('scenes');
    for (const meta of TL.scenes) {
      const el = document.createElement('section'); el.className = 'scene'; el.id = 'scene-' + meta.id;
      el.innerHTML = (window.SCENES[meta.id] || (() => `<div class="center"><h1>${meta.title}</h1></div>`))(meta);
      root.appendChild(el);
      const scene = { id: meta.id, el, start: meta.start, end: meta.end, items: [] };
      scenes.push(scene);
    }
    document.getElementById('segs').innerHTML = TL.scenes.map((s) => `<i style="left:${(s.start / TL.duration * 100).toFixed(3)}%;width:${((s.end - s.start) / TL.duration * 100).toFixed(3)}%"></i>`).join('');
    // lengths of SVG paths can only be measured while a scene is laid out, so show each one for a moment
    for (const s of scenes) { s.el.style.display = 'block'; collect(s); s.el.style.display = 'none'; }
    window.renderAt = renderAt;
    window.Stage.scenes = scenes;
    window.stageReady = true;
    renderAt(+(new URLSearchParams(location.search).get('t') || 0));
  }

  window.Stage = { boot, T, clamp, ease, custom, D, TL, BEATS };
})();
