#!/usr/bin/env python3
"""Build Nightdesk-Video.html, the interactive player for the explainer video.

Reads video/build/timeline.json (the chapters and the three quiz beats), embeds
them in the page as a JavaScript constant, and writes the page one folder up,
next to the video, as Nightdesk-Video.html.

Plain Python 3, standard library only. Run it again whenever timeline.json
changes:

    python3 video/src/make_player.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.dirname(HERE)            # .../Nightdesk/video
PROJECT_DIR = os.path.dirname(VIDEO_DIR)     # .../Nightdesk
TIMELINE = os.path.join(VIDEO_DIR, "build", "timeline.json")
OUTPUT = os.path.join(PROJECT_DIR, "Nightdesk-Video.html")

# Pause-and-think questions. Each one is tied to a countdown beat in
# timeline.json; the beat's start and end times are read from the timeline.
# "answer" is the position of the right option (0 = first option).
QUIZZES = []


def fail(message):
    sys.exit("make_player.py: " + message)


def build_chapters(timeline):
    scenes = timeline.get("scenes") or []
    if not scenes:
        fail("timeline.json has no scenes")
    chapters = []
    seen = set()
    for index, scene in enumerate(scenes):
        start = float(scene["start"])
        end = float(scene["end"])
        if scene["id"] in seen:
            fail("duplicate scene id %r" % scene["id"])
        seen.add(scene["id"])
        if end <= start:
            fail("scene %r ends before it starts" % scene["id"])
        chapters.append({
            "num": str(scene["chapter"]) if 1 <= int(scene["chapter"]) <= 10 else "\u2022",   # same numbers as the video: 1-10, plus a start and a wrap-up
            "id": scene["id"],
            "title": scene["title"] if 1 <= int(scene["chapter"]) <= 10 else ("Start here" if int(scene["chapter"]) == 0 else "Wrap-up: " + scene["title"]),
            "label": ("Chapter %d: " % scene["chapter"] + scene["title"]) if 1 <= int(scene["chapter"]) <= 10 else ("Start here" if int(scene["chapter"]) == 0 else "Wrap-up: " + scene["title"]),
            "start": round(start, 3),
            "end": round(end, 3),
        })
    return chapters


def chapter_index_at(chapters, time):
    for index, chapter in enumerate(chapters):
        if chapter["start"] <= time < chapter["end"]:
            return index
    return None


def build_quizzes(timeline, chapters, duration):
    beats = timeline.get("beats") or {}
    quizzes = []
    for number, quiz in enumerate(QUIZZES, start=1):
        beat = beats.get(quiz["beat"])
        if beat is None:
            fail("quiz beat %r is not in timeline.json" % quiz["beat"])
        start = float(beat["start"])
        end = float(beat["end"])
        if end <= start or end > duration + 0.01:
            fail("quiz beat %r has a bad time window" % quiz["beat"])
        if not 0 <= quiz["answer"] < len(quiz["options"]):
            fail("quiz %d has an answer outside its options" % number)
        chapter = chapter_index_at(chapters, start)
        if chapter is None:
            fail("quiz beat %r does not fall inside a chapter" % quiz["beat"])
        quizzes.append({
            "num": number,
            "id": quiz["beat"],
            "chapter": chapter,
            "start": round(start, 3),
            "end": round(end, 3),
            "question": quiz["question"],
            "options": quiz["options"],
            "answer": quiz["answer"],
            "explanation": quiz["explanation"],
        })
    return quizzes


def main():
    if not os.path.exists(TIMELINE):
        fail("cannot find %s" % TIMELINE)
    with open(TIMELINE, encoding="utf-8") as handle:
        timeline = json.load(handle)

    duration = float(timeline["duration"])
    chapters = build_chapters(timeline)
    quizzes = build_quizzes(timeline, chapters, duration)

    data = {
        "duration": round(duration, 3),
        "chapters": chapters,
        "quizzes": quizzes,
    }
    blob = json.dumps(data, ensure_ascii=False, indent=1)
    # Stop the data from ever closing the <script> tag early.
    blob = blob.replace("</", "<\\/")
    html = TEMPLATE.replace("__PLAYER_DATA__", blob)

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        handle.write(html)

    print("Wrote %s" % OUTPUT)
    print("%d chapters, %d quizzes, %.1f minutes" % (len(chapters), len(quizzes), duration / 60.0))


TEMPLATE = r"""<!doctype html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Nightdesk, explained</title>
<!-- Built by video/src/make_player.py from video/build/timeline.json. Change the generator, then run it again. -->
<style>
:root {
  --bg: #0B1220;
  --panel: #131C2E;
  --line: #2A3856;
  --text: #ECE8DD;
  --soft: #B4BCCD;
  --amber: #F2B24C;
  --green: #57C795;
  --red: #F0717A;
  --serif: ui-serif, "New York", Georgia, serif;
  --sans: system-ui, -apple-system, sans-serif;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 16px; line-height: 1.45; }
body { min-height: 100vh; }
button { font: inherit; color: inherit; }
:focus-visible { outline: 3px solid var(--amber); outline-offset: 3px; }

.wrap { max-width: 1240px; margin: 0 auto; padding: 18px 24px 48px; }
header h1 { font-family: var(--serif); font-weight: 600; font-size: 32px; line-height: 1.15; letter-spacing: -0.01em; margin: 0 0 6px; }
header p { margin: 0; color: var(--soft); font-size: 17px; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 24px; margin-top: 16px; align-items: start; }
@media (max-width: 1000px) { .layout { grid-template-columns: minmax(0, 1fr); } }

/* Buttons: at least 44 px tall */
.btn { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 8px 16px; border: 2px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--text); font-size: 16px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.btn:hover:not(:disabled) { border-color: #43557F; }
.btn.on { border-color: var(--amber); color: var(--amber); }
.btn.primary { background: var(--amber); border-color: var(--amber); color: #0B1220; }
.btn.primary:hover:not(:disabled) { filter: brightness(1.06); }

/* Video */
.stage { position: relative; background: #000; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.stage video { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; background: #000; }
.stage-msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 24px; text-align: center; background: var(--bg); color: var(--soft); font-size: 18px; }

/* Pause-and-think overlay */
.quiz { position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column; justify-content: center; gap: 12px; padding: 18px clamp(14px, 3vw, 28px); background: rgba(11, 18, 32, 0.96); overflow-y: auto; }
.kicker { margin: 0; color: var(--amber); font-size: 13px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }
.quiz h2 { margin: 0; font-size: clamp(18px, 2.2vw, 24px); line-height: 1.3; }
.opts { display: grid; gap: 8px; }
.opt { min-height: 46px; padding: 10px 16px; text-align: left; font-size: 18px; font-weight: 600; background: var(--panel); color: var(--text); border: 2px solid var(--line); border-radius: 10px; cursor: pointer; }
.opt:hover:not(:disabled) { border-color: var(--amber); }
.opt:disabled { cursor: default; }
.opt.right { border-color: var(--green); color: var(--green); }
.opt.wrong { border-color: var(--red); color: var(--red); }
.opt.faded { opacity: 0.5; }
.verdict { margin: 0; font-size: 20px; font-weight: 700; }
.verdict.right { color: var(--green); }
.verdict.wrong { color: var(--red); }
.explain { margin: 4px 0 0; color: var(--soft); font-size: 16px; }
.quiz-foot { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; justify-content: space-between; }
.quiz-foot p { margin: 0; font-size: 16px; color: var(--soft); }

/* Under the video */
.resume { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 12px 14px; background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--amber); border-radius: 12px; }
.resume p { margin: 0; }
.row { display: flex; flex-wrap: wrap; gap: 8px; }
.segbar { display: flex; gap: 4px; margin-top: 12px; }
.seg { position: relative; flex: 1 1 0; min-width: 0; min-height: 44px; padding: 0; background: none; border: 0; border-radius: 6px; cursor: pointer; }
.seg-track { position: absolute; left: 0; right: 0; top: 50%; height: 12px; transform: translateY(-50%); border-radius: 6px; background: var(--line); overflow: hidden; }
.seg:hover .seg-track { background: #34446A; }
.seg-fill { position: absolute; left: 0; top: 0; bottom: 0; width: 0; background: var(--soft); transition: width 0.25s linear; }
.seg.current .seg-track { box-shadow: 0 0 0 2px var(--amber); }
.seg.current .seg-fill { background: var(--amber); }
.now { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; margin-top: 8px; color: var(--soft); font-size: 15px; }
.now span:first-child { color: var(--text); }
.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
.speed-label { margin: 0 2px 0 8px; color: var(--soft); font-size: 15px; }
.keys { margin: 10px 0 0; color: var(--soft); font-size: 14px; line-height: 1.9; }
kbd { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; color: var(--text); background: var(--panel); border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 6px; padding: 1px 6px; }
.notice { margin: 12px 0 0; padding: 10px 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; font-size: 15px; }

/* Chapter list */
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.panel h2 { font-family: var(--serif); font-weight: 600; font-size: 24px; line-height: 1.2; margin: 0 0 4px; }
.hint { margin: 0 0 10px; color: var(--soft); font-size: 14px; }
.chapters { list-style: none; margin: 0; padding: 0 4px 0 0; display: grid; gap: 6px; }
@media (min-width: 1001px) { .chapters { max-height: 600px; overflow-y: auto; } }
.ch { width: 100%; min-height: 48px; display: grid; grid-template-columns: 28px minmax(0, 1fr) auto 22px; align-items: center; gap: 10px; padding: 8px 10px; background: transparent; border: 2px solid transparent; border-radius: 10px; text-align: left; cursor: pointer; color: var(--text); }
.ch:hover { background: rgba(242, 178, 76, 0.06); border-color: var(--line); }
.ch.current { border-color: var(--amber); background: rgba(242, 178, 76, 0.1); }
.ch-n { font-weight: 700; color: var(--soft); font-variant-numeric: tabular-nums; }
.ch.current .ch-n { color: var(--amber); }
.ch-title { font-size: 15px; line-height: 1.3; }
.ch-len { color: var(--soft); font-size: 14px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ch-tick { display: inline-flex; color: var(--green); opacity: 0; }
.ch-tick.done { opacity: 1; }
.ch-tick svg { width: 20px; height: 20px; }
.quiz-tag { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px; background: var(--amber); color: #0B1220; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; vertical-align: 2px; }
.score { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); color: var(--soft); font-size: 15px; }
.score p { margin: 0 0 4px; }
.score #scoreLine { color: var(--text); font-weight: 700; }

@media (prefers-reduced-motion: reduce) { .seg-fill { transition: none; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Nightdesk, explained</h1>
    <p>About 12 minutes. Pretend money only. Not financial advice.</p>
  </header>

  <main class="layout">
    <section aria-label="Video">
      <div class="resume" id="resume" hidden>
        <p id="resumeText"></p>
        <div class="row">
          <button class="btn primary" id="resumeGo" type="button">Resume</button>
          <button class="btn" id="resumeStart" type="button">Start over</button>
        </div>
      </div>

      <div class="stage" id="stage">
        <video id="video" src="Nightdesk-Explained.mp4" controls preload="metadata" playsinline></video>
        <div class="stage-msg" id="stageMsg" role="alert" hidden></div>
        <div class="quiz" id="quiz" role="dialog" aria-modal="true" aria-labelledby="quizQ" hidden>
          <p class="kicker" id="quizKicker"></p>
          <h2 id="quizQ"></h2>
          <div class="opts" id="quizOpts"></div>
          <div id="quizAns" hidden>
            <p class="verdict" id="quizVerdict" aria-live="polite"></p>
            <p class="explain" id="quizExplain"></p>
          </div>
          <div class="quiz-foot">
            <p id="quizScore"></p>
            <button class="btn primary" id="quizNext" type="button" hidden>Continue</button>
          </div>
        </div>
      </div>

      <div id="pieces">
        <div class="segbar" id="segbar"></div>
        <div class="now"><span id="nowChapter"></span><span id="nowTime"></span></div>

        <div class="controls">
          <button class="btn" id="btnBack" type="button">Back 10 s</button>
          <button class="btn primary" id="btnPlay" type="button">Play</button>
          <button class="btn" id="btnFwd" type="button">Forward 10 s</button>
          <span class="speed-label">Speed</span>
          <button class="btn speed" data-speed="0.75" type="button" aria-pressed="false">0.75x</button>
          <button class="btn speed" data-speed="1" type="button" aria-pressed="true">1x</button>
          <button class="btn speed" data-speed="1.25" type="button" aria-pressed="false">1.25x</button>
          <button class="btn speed" data-speed="1.5" type="button" aria-pressed="false">1.5x</button>
        </div>

        <div class="controls">
          <button class="btn" id="btnMusic" type="button" aria-pressed="true" title="On plays the version with music. Off plays the voice-only version.">Music: on</button>
          <button class="btn" id="btnQuiz" type="button" aria-pressed="true" title="When on, the video stops for a quick question.">Quiz mode: on</button>
        </div>

        <p class="keys">
          <kbd>Space</kbd> play/pause &nbsp;
          <kbd>&larr;</kbd> <kbd>&rarr;</kbd> 10 s back/forward &nbsp;
          <kbd>N</kbd>/<kbd>P</kbd> next/previous chapter &nbsp;
          <kbd>Q</kbd> quiz &nbsp;
          <kbd>M</kbd> music &nbsp;
          <kbd>1</kbd>&ndash;<kbd>4</kbd> speed
        </p>
        <p class="notice" id="notice" role="status" hidden></p>
      </div>
    </section>

    <aside class="panel" id="chapterPanel" aria-label="Chapters">
      <h2>Chapters</h2>
      <p class="hint">Click a chapter to jump to it. A green tick means you have watched it.</p>
      <ul class="chapters" id="chapterList"></ul>
      <div class="score">
        <p id="watchedLine"></p>
        <p id="scoreLine"></p>
      </div>
    </aside>
  </main>
</div>

<script>
(function () {
  'use strict';

  // Chapters and quizzes, written in by make_player.py.
  var D = __PLAYER_DATA__;

  var MAIN = 'Nightdesk-Explained.mp4';
  var VOICE = 'Nightdesk-Explained-voice-only.mp4';
  var KEY_RESUME = 'nd-video-resume';
  var KEY_WATCHED = 'nd-video-watched';
  var KEY_QUIZ = 'nd-video-quiz';
  var SPEEDS = [0.75, 1, 1.25, 1.5];
  var JUMP = 10;
  var chapters = D.chapters;
  var quizzes = D.quizzes;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  function fmtLen(sec) { return fmt(Math.round(sec)); }

  // Browser storage can be blocked (private windows), so every call is guarded.
  function load(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* the page still works */ }
  }
  function forget(key) {
    try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  var state = {
    music: true,
    quizMode: true,
    quizOpen: null,       // the quiz on screen, or null
    speed: 1,
    seeking: false,
    swap: null,           // set while switching between music and voice-only
    lastT: 0,             // the time at the last check, used to spot crossings
    lastSave: 0,
    keepUntil: null,      // a saved resume point we do not overwrite yet
    offer: null,          // the time the Resume button will go to
    watched: load(KEY_WATCHED, []),
    answers: load(KEY_QUIZ, {})
  };
  if (!Array.isArray(state.watched)) state.watched = [];
  if (!state.answers || typeof state.answers !== 'object') state.answers = {};
  quizzes.forEach(function (q) { q.fired = false; });

  var video = $('video');
  var btnPlay = $('btnPlay');
  var chapterEls = [], tickEls = [], segEls = [], segFills = [];
  var speedBtns = Array.prototype.slice.call(document.querySelectorAll('.speed'));
  var noticeTimer = null;

  function chapterAt(t) {
    for (var i = 0; i < chapters.length; i++) {
      if (t < chapters[i].end) return i;
    }
    return chapters.length - 1;
  }

  // ---- Build the chapter list and the bar under the video ----
  function buildUI() {
    var list = $('chapterList');
    var bar = $('segbar');
    chapters.forEach(function (c, i) {
      var label = c.label;

      var b = el('button', 'ch');
      b.type = 'button';
      b.setAttribute('aria-label', label + '. ' + fmtLen(c.end - c.start) + ' long.');
      var titleBox = el('span', 'ch-title', c.title);
      quizzes.forEach(function (q) {
        if (q.chapter === i) titleBox.appendChild(el('span', 'quiz-tag', 'Quiz'));
      });
      var tick = el('span', 'ch-tick');
      tick.setAttribute('aria-hidden', 'true');
      tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
      b.appendChild(el('span', 'ch-n', String(c.num)));
      b.appendChild(titleBox);
      b.appendChild(el('span', 'ch-len', fmtLen(c.end - c.start)));
      b.appendChild(tick);
      b.addEventListener('click', function () { goChapter(i); });

      var li = el('li');
      li.appendChild(b);
      list.appendChild(li);
      chapterEls.push(b);
      tickEls.push(tick);

      var s = el('button', 'seg');
      s.type = 'button';
      s.style.flexGrow = String(c.end - c.start);
      s.title = label;
      s.setAttribute('aria-label', label);
      var track = el('span', 'seg-track');
      var fill = el('span', 'seg-fill');
      track.appendChild(fill);
      s.appendChild(track);
      s.addEventListener('click', function () { goChapter(i); });
      bar.appendChild(s);
      segEls.push(s);
      segFills.push(fill);
    });
  }

  // Shows where we are. Runs on every time change, so it only sets what changed.
  function refresh(t) {
    var cur = chapterAt(t);
    chapters.forEach(function (c, i) {
      var on = i === cur;
      chapterEls[i].classList.toggle('current', on);
      if (on) chapterEls[i].setAttribute('aria-current', 'true');
      else chapterEls[i].removeAttribute('aria-current');
      tickEls[i].classList.toggle('done', state.watched.indexOf(c.id) !== -1);
      segEls[i].classList.toggle('current', on);
      var p = clamp((t - c.start) / (c.end - c.start), 0, 1);
      segFills[i].style.width = (p * 100).toFixed(2) + '%';
    });
    $('nowChapter').textContent = chapters[cur].label;
    $('nowTime').textContent = fmt(t) + ' of ' + fmt(D.duration);
  }

  function renderScore() {
    var right = quizzes.filter(function (q) { return state.answers[q.id] === true; }).length;
    var watched = chapters.filter(function (c) { return state.watched.indexOf(c.id) !== -1; }).length;
    var line = 'Quizzes: ' + right + ' of ' + quizzes.length + ' right';
    $('scoreLine').textContent = line;
    $('quizScore').textContent = line;
    $('watchedLine').textContent = 'Chapters watched: ' + watched + ' of ' + chapters.length;
  }

  function updateButtons() {
    var m = $('btnMusic');
    m.textContent = 'Music: ' + (state.music ? 'on' : 'off');
    m.setAttribute('aria-pressed', String(state.music));
    m.classList.toggle('on', state.music);
    var q = $('btnQuiz');
    q.textContent = 'Quiz mode: ' + (state.quizMode ? 'on' : 'off');
    q.setAttribute('aria-pressed', String(state.quizMode));
    q.classList.toggle('on', state.quizMode);
  }

  function setSpeed(s) {
    state.speed = s;
    video.playbackRate = s;
    speedBtns.forEach(function (b) {
      var on = Number(b.getAttribute('data-speed')) === s;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  function notice(text) {
    var n = $('notice');
    n.textContent = text;
    n.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(function () { n.hidden = true; }, 9000);
  }

  function showStageMsg(text) {
    var m = $('stageMsg');
    m.textContent = text;
    m.hidden = false;
  }

  // ---- Playback helpers ----
  function play() {
    var p = video.play();
    if (p && typeof p.catch === 'function') p.catch(function () { /* e.g. file missing: shown elsewhere */ });
  }
  function togglePlay() {
    if (video.paused) play(); else video.pause();
  }
  function skip(sec) {
    video.currentTime = clamp(video.currentTime + sec, 0, D.duration);
  }
  function goChapter(i) {
    hideResume();
    video.currentTime = chapters[i].start;
    play();
  }
  function nextChapter() {
    var i = chapterAt(video.currentTime) + 1;
    if (i < chapters.length) goChapter(i);
  }
  function prevChapter() {
    goChapter(Math.max(0, chapterAt(video.currentTime) - 1));
  }
  function setQuizMode(on) {
    state.quizMode = on;
    updateButtons();
  }

  // Switch between the music and voice-only files, keeping time and play state.
  function setMusic(on) {
    if (on === state.music && !state.swap) return;
    var s = state.swap;
    var t = s ? s.t : video.currentTime;
    var playing = s ? s.playing : !video.paused;
    state.music = on;
    state.swap = { t: t, playing: playing, to: on ? MAIN : VOICE, pending: false };
    updateButtons();
    video.src = on ? MAIN : VOICE;
  }

  function onVideoError() {
    var s = state.swap;
    if (s && s.to === VOICE) {
      // The voice-only file is missing: go back to the music version.
      state.music = true;
      state.swap = { t: s.t, playing: s.playing, to: MAIN, pending: false };
      updateButtons();
      notice('The voice-only version is not in this folder yet, so the music version is playing.');
      video.src = MAIN;
      return;
    }
    state.swap = null;
    showStageMsg('The video file should sit next to this page. Put Nightdesk-Explained.mp4 in the same folder as this page, then reload.');
  }

  // ---- Quiz ----
  function setInert(on) {
    ['video', 'pieces', 'chapterPanel'].forEach(function (id) { $(id).inert = on; });
  }

  function openQuiz(q) {
    state.quizOpen = q;
    video.pause();
    $('quizKicker').textContent = 'Quiz ' + q.num + ' of ' + quizzes.length + ' · Pause and think';
    $('quizQ').textContent = q.question;
    var box = $('quizOpts');
    box.textContent = '';
    q.options.forEach(function (text, idx) {
      var b = el('button', 'opt', text);
      b.type = 'button';
      b.addEventListener('click', function () { answer(q, idx); });
      box.appendChild(b);
    });
    $('quizAns').hidden = true;
    $('quizNext').hidden = true;
    renderScore();
    $('quiz').hidden = false;
    setInert(true);
    box.firstChild.focus();
  }

  function answer(q, idx) {
    var ok = idx === q.answer;
    var buttons = $('quizOpts').children;
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].disabled = true;
      if (i === q.answer) buttons[i].classList.add('right');
      else if (i === idx) buttons[i].classList.add('wrong');
      else buttons[i].classList.add('faded');
    }
    var v = $('quizVerdict');
    v.textContent = ok ? 'Right.' : 'Wrong.';
    v.className = 'verdict ' + (ok ? 'right' : 'wrong');
    $('quizExplain').textContent = q.explanation;
    $('quizAns').hidden = false;
    $('quizNext').hidden = false;
    state.answers[q.id] = ok;
    save(KEY_QUIZ, state.answers);
    renderScore();
    $('quizNext').focus();
  }

  function closeQuiz() {
    state.quizOpen = null;
    $('quiz').hidden = true;
    setInert(false);
  }

  // Plays past a quiz start: pause and ask. Each quiz fires once per pass.
  function checkPlayback(t) {
    if (video.paused) return;
    var prev = state.lastT;
    if (state.quizMode && !state.quizOpen) {
      for (var i = 0; i < quizzes.length; i++) {
        var q = quizzes[i];
        if (!q.fired && prev < q.start && t >= q.start) {
          q.fired = true;
          openQuiz(q);
          return;
        }
      }
    }
    chapters.forEach(function (c) {
      if (prev < c.end && t >= c.end) markWatched(c.id);
    });
  }

  function markWatched(id) {
    if (state.watched.indexOf(id) !== -1) return;
    state.watched.push(id);
    save(KEY_WATCHED, state.watched);
    renderScore();
  }

  // ---- Resume ----
  function saveResume() {
    state.lastSave = Date.now();
    if (state.swap) return;
    var t = video.currentTime;
    if (!(t > 1)) return;
    if (state.keepUntil !== null) {
      if (t < state.keepUntil) return;
      state.keepUntil = null;
    }
    if (t > D.duration - 15) { forget(KEY_RESUME); return; }
    save(KEY_RESUME, { t: Math.floor(t) });
  }
  function showResume(t) {
    state.offer = t;
    state.keepUntil = t;
    $('resumeText').textContent = 'You stopped at ' + fmt(t) + '. Pick up where you left off?';
    $('resumeGo').textContent = 'Resume from ' + fmt(t);
    $('resume').hidden = false;
  }
  function hideResume() {
    $('resume').hidden = true;
  }

  // ---- Video events ----
  video.addEventListener('loadedmetadata', function () {
    $('stageMsg').hidden = true;
    video.playbackRate = state.speed;
    var s = state.swap;
    if (!s) return;
    state.lastT = s.t;
    if (s.t > 0.05) {
      s.pending = true;
      video.currentTime = s.t;
    } else {
      state.swap = null;
    }
    if (s.playing) play();
  });

  video.addEventListener('seeking', function () {
    state.seeking = true;
    hideResume();
  });

  // After any jump, a quiz only re-arms if we jumped back before its start.
  video.addEventListener('seeked', function () {
    state.seeking = false;
    if (state.swap && state.swap.pending) state.swap = null;
    var t = video.currentTime;
    quizzes.forEach(function (q) { q.fired = t >= q.start; });
    state.lastT = t;
    refresh(t);
  });

  video.addEventListener('timeupdate', function () {
    var t = video.currentTime;
    if (!state.seeking && !state.swap) checkPlayback(t);
    state.lastT = t;
    refresh(t);
    if (!video.paused && Date.now() - state.lastSave > 3000) saveResume();
  });

  video.addEventListener('play', function () {
    btnPlay.textContent = 'Pause';
    hideResume();
  });
  video.addEventListener('pause', function () {
    btnPlay.textContent = 'Play';
    saveResume();
  });
  video.addEventListener('ended', function () {
    markWatched(chapters[chapters.length - 1].id);
    forget(KEY_RESUME);
    btnPlay.textContent = 'Play';
  });
  video.addEventListener('error', onVideoError);
  window.addEventListener('pagehide', saveResume);

  // ---- Buttons ----
  btnPlay.addEventListener('click', togglePlay);
  $('btnBack').addEventListener('click', function () { skip(-JUMP); });
  $('btnFwd').addEventListener('click', function () { skip(JUMP); });
  $('btnMusic').addEventListener('click', function () { setMusic(!state.music); });
  $('btnQuiz').addEventListener('click', function () { setQuizMode(!state.quizMode); });
  speedBtns.forEach(function (b) {
    b.addEventListener('click', function () { setSpeed(Number(b.getAttribute('data-speed'))); });
  });

  $('quizNext').addEventListener('click', function () {
    var q = state.quizOpen;
    if (!q) return;
    closeQuiz();
    video.currentTime = q.end;
    play();
    btnPlay.focus();
  });

  $('resumeGo').addEventListener('click', function () {
    var t = state.offer;
    hideResume();
    if (t !== null) video.currentTime = t;
    play();
  });
  $('resumeStart').addEventListener('click', function () {
    hideResume();
    state.keepUntil = null;
    forget(KEY_RESUME);
    video.currentTime = 0;
    play();
  });

  // ---- Keyboard ----
  function isTyping(node) {
    return !!node && (node.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName));
  }

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || state.quizOpen || isTyping(e.target)) return;
    var k = e.key;
    var handled = true;
    if (k === ' ' || k === 'Spacebar') {
      if (!e.repeat) togglePlay();
    }
    else if (k === 'ArrowLeft') skip(-JUMP);
    else if (k === 'ArrowRight') skip(JUMP);
    else if (k === 'n' || k === 'N') nextChapter();
    else if (k === 'p' || k === 'P') prevChapter();
    else if (k === 'q' || k === 'Q') setQuizMode(!state.quizMode);
    else if (k === 'm' || k === 'M') setMusic(!state.music);
    else if (k === '1' || k === '2' || k === '3' || k === '4') setSpeed(SPEEDS[Number(k) - 1]);
    else handled = false;
    if (handled) e.preventDefault();
  });
  // Stop a focused button from also firing when Space is used as play/pause.
  document.addEventListener('keyup', function (e) {
    if ((e.key === ' ' || e.key === 'Spacebar') && !state.quizOpen && !isTyping(e.target)) e.preventDefault();
  });

  // ---- Start ----
  buildUI();
  setSpeed(1);
  updateButtons();
  renderScore();
  refresh(0);

  var saved = load(KEY_RESUME, null);
  if (saved && typeof saved.t === 'number' && saved.t > 10 && saved.t < D.duration - 15) {
    showResume(saved.t);
  }
  if (video.error) onVideoError();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
