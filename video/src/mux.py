"""Put the pictures and the sound together. Writes ../Nightdesk-Explained.mp4 and ../Nightdesk-Explained-voice-only.mp4.

    .venv/bin/python src/mux.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FF, PROBE = os.environ.get("FFMPEG", "ffmpeg"), os.environ.get("FFPROBE", "ffprobe")
video = HERE / "build" / "video.mp4"
want = json.loads((HERE / "build" / "timeline.json").read_text())["duration"]


def probe(path: Path) -> dict:
    out = subprocess.run([PROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


for audio, name in (("audio_full.wav", "Nightdesk-Explained.mp4"), ("audio_voice.wav", "Nightdesk-Explained-voice-only.mp4")):
    src = HERE / "build" / audio
    if not src.exists():
        sys.exit(f"missing {src}: run src/audio.py first")
    out = HERE.parent / name
    subprocess.run([FF, "-v", "error", "-y", "-i", str(video), "-i", str(src), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart",
                    "-metadata", "title=Nightdesk, explained", str(out)], check=True)
    info = probe(out)
    dur = float(info["format"]["duration"])
    kinds = sorted(f"{s['codec_type']}:{s['codec_name']}" for s in info["streams"])
    ok = abs(dur - want) < 0.5 and kinds == ["audio:aac", "video:h264"]
    print(f"{name}: {dur:.1f} s (timeline {want:.1f} s), {', '.join(kinds)}, {out.stat().st_size / 1e6:.0f} MB {'OK' if ok else 'CHECK THIS'}")
