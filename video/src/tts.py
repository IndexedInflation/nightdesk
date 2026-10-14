"""Record the narration with a local voice model (Kokoro) and lay it out on a timeline.

Reads build/script.json. Writes:
  build/voice/<beat>.wav     one clip per spoken line (kept, so a re-run only records lines that changed)
  build/narration.wav        every clip in place, with the quiet gaps between them
  build/timeline.json        when every beat and scene starts and ends, caption chunks, and a loudness curve

Run in isolated mode because it loads downloaded model files:
    .venv/bin/python -I src/tts.py --model models/kokoro-v1.0.onnx --voices models/voices-v1.0.bin
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parents[1]
BUILD = HERE / "build"
RATE = 24000
LEAD = 0.05            # a breath of quiet kept before each clip
TAIL = 0.10            # and after it


def trim(x: np.ndarray, floor: float = 0.006) -> np.ndarray:
    """Cut the silence the model leaves at both ends, keeping a short breath."""
    loud = np.flatnonzero(np.abs(x) > floor)
    if not len(loud):
        return x
    a, b = max(0, loud[0] - int(LEAD * RATE)), min(len(x), loud[-1] + int(TAIL * RATE))
    return x[a:b]


def chunks(text: str, start: float, end: float) -> list[dict]:
    """Split a line into short caption pieces, timed by how many letters each has."""
    words = text.split()
    parts, cur = [], []
    for w in words:
        cur.append(w)
        line = " ".join(cur)
        plain = line.replace("*", "")
        if len(plain) >= 34 or (re.search(r"[.?!,]\*?$", w) and len(plain) >= 16):
            parts.append(line)
            cur = []
    if cur:
        if parts and len(" ".join(cur).replace("*", "")) < 9:
            parts[-1] += " " + " ".join(cur)
        else:
            parts.append(" ".join(cur))
    # a highlight opened in one piece and closed in the next: close and reopen it so each piece stands alone
    fixed, open_star = [], False
    for p in parts:
        if open_star:
            p = "*" + p
        open_star = p.count("*") % 2 == 1
        if open_star:
            p += "*"
        fixed.append(p)
    weights = np.array([len(p.replace("*", "")) + 4 for p in fixed], dtype=float)
    edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [{"text": p, "start": round(float(a), 3), "end": round(float(b), 3)} for p, a, b in zip(fixed, edges[:-1], edges[1:])]


GLUE = {"a", "an", "the", "of", "to", "in", "at", "on", "by", "and", "or", "but", "for", "from", "with", "my", "your", "its", "that", "than", "as", "is", "it", "into"}


def caption_chunks(text: str, start: float, end: float, limit: int = 42) -> list[dict]:
    """Caption pieces for the screen (separate from the timing anchors above, so changing these never moves an animation).
    Never splits "7:00 am", prefers to break after punctuation, never ends a piece on a little word like "of"."""
    words = re.sub(r"(\d) (am|pm)\b", lambda m: m.group(1) + "\u00a0" + m.group(2), text).split()
    plain = lambda ws: " ".join(ws).replace("*", "").replace("\u00a0", " ")
    parts, cur = [], []
    for w in words:
        if cur and len(plain(cur + [w])) > limit:
            cut = len(cur)
            for k in range(len(cur) - 1, 0, -1):                       # the last punctuation that leaves a decent piece
                if re.search(r"[.?!,;:]\*?$", cur[k - 1]) and len(plain(cur[:k])) >= 18:
                    cut = k
                    break
            while cut > 1 and cur[cut - 1].strip("*").lower() in GLUE:     # do not leave "of" or "the" hanging
                cut -= 1
            parts.append(cur[:cut])
            cur = cur[cut:]
        cur.append(w)
        if re.search(r"[.?!]\*?$", w) and len(plain(cur)) >= 26:           # a full sentence of decent length: its own piece
            parts.append(cur)
            cur = []
    if cur:
        if parts and len(plain(cur)) < 10 and len(plain(parts[-1] + cur)) <= limit + 10:
            parts[-1] = parts[-1] + cur
        else:
            parts.append(cur)
    texts = [" ".join(p).replace("\u00a0", " ") for p in parts]
    fixed, open_star = [], False
    for p in texts:
        if open_star:
            p = "*" + p
        open_star = p.count("*") % 2 == 1
        if open_star:
            p += "*"
        fixed.append(p)
    weights = np.array([len(p.replace("*", "")) + 4 for p in fixed], dtype=float)
    edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [{"text": p, "start": round(float(a), 3), "end": round(float(b), 3)} for p, a, b in zip(fixed, edges[:-1], edges[1:])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--voices", required=True)
    ap.add_argument("--only", default=None, help="record just these beat ids (comma separated), for a quick listen")
    args = ap.parse_args()
    script = json.loads((BUILD / "script.json").read_text())
    voice_dir = BUILD / "voice"
    voice_dir.mkdir(exist_ok=True)
    only = set(args.only.split(",")) if args.only else None

    from kokoro_onnx import Kokoro
    kokoro = Kokoro(args.model, args.voices)

    t, track, scenes, beats = 0.0, [], [], {}
    made = reused = 0
    t0 = time.time()
    for scene in script["scenes"]:
        s_start = t
        for b in scene["beats"]:
            if "silence" in b:
                n = int(b["silence"] * RATE)
                track.append(np.zeros(n, dtype=np.float32))
                beats[b["id"]] = {"start": round(t, 3), "end": round(t + b["silence"], 3), "tag": b["tag"], "scene": scene["id"]}
                t += n / RATE
                continue
            if only and b["id"] not in only:
                continue
            key = hashlib.sha1(f"{script['voice']}|{script['speed']}|{b['tts']}".encode()).hexdigest()[:12]
            path = voice_dir / f"{b['id']}.{key}.wav"
            if path.exists():
                clip, _ = sf.read(path, dtype="float32")
                reused += 1
            else:
                for old in voice_dir.glob(f"{b['id']}.*.wav"):
                    old.unlink()
                samples, rate = kokoro.create(b["tts"], voice=script["voice"], speed=script["speed"], lang="en-us")
                assert rate == RATE, rate
                clip = trim(np.asarray(samples, dtype=np.float32))
                sf.write(path, clip, RATE)
                made += 1
            dur = len(clip) / RATE
            words = len(b["tts"].split())
            beats[b["id"]] = {"start": round(t, 3), "end": round(t + dur, 3), "say": b["say"], "scene": scene["id"], "words": words,
                              "wpm": round(words / dur * 60), "chunks": chunks(b["say"], t, t + dur),
                              "caps": caption_chunks(b["say"], t, t + dur)}
            gap = np.zeros(int(b["gap"] * RATE), dtype=np.float32)
            track += [clip, gap]
            t += (len(clip) + len(gap)) / RATE
        scenes.append({"id": scene["id"], "title": scene["title"], "chapter": scene["chapter"], "start": round(s_start, 3), "end": round(t, 3)})
    audio = np.concatenate(track) if track else np.zeros(1, dtype=np.float32)
    peak = float(np.abs(audio).max()) or 1.0
    audio = audio * (0.89 / peak)                                    # one gain for the whole track: no line louder than another
    sf.write(BUILD / "narration.wav", audio, RATE)

    fps = script["fps"]
    hop = RATE // fps                                                # one loudness reading per video frame, for the "speaking" light
    frames = len(audio) // hop
    env = np.sqrt((audio[:frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    env = np.minimum(1.0, env / (np.percentile(env[env > 0.01], 90) if (env > 0.01).any() else 1.0))
    timeline = {"fps": fps, "duration": round(len(audio) / RATE, 3), "scenes": scenes, "beats": beats,
                "env": [round(float(v), 2) for v in env]}
    (BUILD / "timeline.json").write_text(json.dumps(timeline))
    spoken = [v for v in beats.values() if "wpm" in v]
    rates = sorted(v["wpm"] for v in spoken)
    print(f"recorded {made}, reused {reused} in {time.time() - t0:.0f}s | total {timeline['duration'] / 60:.2f} min | "
          f"pace median {rates[len(rates) // 2]} words a minute (slowest {rates[0]}, fastest {rates[-1]})")
    odd = [(k, v["wpm"], v["say"][:50]) for k, v in beats.items() if "wpm" in v and v["words"] >= 5 and not 110 <= v["wpm"] <= 235]
    for k, wpm, text in odd:
        print(f"  CHECK pace {k}: {wpm} wpm  {text}")
    for s in scenes:
        print(f"  {s['id']:<6} {s['start'] / 60:5.2f} -> {s['end'] / 60:5.2f} min  ({s['end'] - s['start']:.0f}s)  {s['title']}")


if __name__ == "__main__":
    sys.exit(main())
