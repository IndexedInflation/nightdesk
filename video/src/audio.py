"""Build the soundtrack for the Nightdesk explainer video.

Reads build/narration.wav and build/timeline.json. Writes:
  build/audio_full.wav   voice + very quiet pad music + soft effects (48 kHz, 16-bit, stereo)
  build/audio_voice.wav  voice only (48 kHz, 16-bit, stereo)

The music and effects are synthesized here with numpy only. ffmpeg does the
48 kHz resampling and the two-pass loudnorm. Intermediate files go in a temporary
folder that is deleted at the end.

    .venv/bin/python src/audio.py
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parents[1]
BUILD = HERE / "build"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
SR = 48000
FPS = 30
SEED = 20261014

LOUD = "I=-16:TP=-1.5:LRA=11"
MUSIC_DB = -28.0          # music RMS relative to the voice RMS while someone is speaking
SILENCE_RISE_DB = 6.0     # music comes up this much in silences (chapter cards, countdowns)
SPEECH_FULL = 0.15        # envelope value at which a frame counts as fully "speaking"
DUCK_ATTACK_S = 0.3       # music drops this fast when speech starts
DUCK_RELEASE_S = 0.8      # and comes back this slowly when speech stops
PEAK_LIMIT = 0.9          # keep the mix under this before loudnorm (voice already peaks near 0.89)

# Slow pad: C - Am - F - G, 8 s per chord, 1.5 s crossfades. (root Hz, pad notes Hz)
CHORDS = [
    (65.41, (164.81, 196.00, 261.63, 329.63)),   # C
    (110.00, (130.81, 164.81, 220.00)),          # Am
    (87.31, (174.61, 220.00, 261.63, 349.23)),   # F
    (98.00, (196.00, 246.94, 293.66, 392.00)),   # G
]
CHORD_S = 8.0
XFADE_S = 1.5
DETUNE = 0.0015           # +/-0.15 % detuned copies, one to each side, for width
PAN = (-0.20, 0.15, -0.10, 0.25)


def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{res.stderr[-2000:]}")
    return res.stderr


def rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x)))


def db(x):
    return 20 * np.log10(max(x, 1e-12))


# ---------------------------------------------------------------- voice

def load_narration_48k(tmp):
    path = tmp / "narration_48k.wav"
    ffmpeg("-i", str(BUILD / "narration.wav"), "-ar", str(SR), "-ac", "1",
           "-c:a", "pcm_f32le", str(path))
    x, sr = sf.read(path, dtype="float32")
    assert sr == SR and x.ndim == 1, (sr, x.shape)
    return x


def fit(x, n):
    out = np.zeros(n, dtype=np.float32)
    m = min(n, len(x))
    out[:m] = x[:m]
    return out


def speaking_curve(env):
    """0 = silence, 1 = speaking, per video frame. Attack/release smoothing so the duck does not pump."""
    target = np.clip(np.asarray(env) / SPEECH_FULL, 0.0, 1.0)
    dt = 1.0 / FPS
    a_att = 1 - np.exp(-dt / DUCK_ATTACK_S)
    a_rel = 1 - np.exp(-dt / DUCK_RELEASE_S)
    out = np.empty_like(target)
    cur = 0.0
    for i, x in enumerate(target):
        cur += (a_att if x > cur else a_rel) * (x - cur)
        out[i] = cur
    return out


# ---------------------------------------------------------------- music

def pad_music(n):
    """Chord pad, (n, 2) float32, level not set yet."""
    out = np.zeros((n, 2), dtype=np.float32)
    total = n / SR
    half = XFADE_S / 2
    k = 0
    while CHORD_S * k < total:
        root, notes = CHORDS[k % len(CHORDS)]
        t_on = CHORD_S * k - half
        t_off = CHORD_S * (k + 1) + half
        i0 = max(0, int(np.floor(t_on * SR)))
        i1 = min(n, int(np.ceil(t_off * SR)))
        t = np.arange(i0, i1) / SR
        if k == 0:
            up = np.ones_like(t)
        else:
            up = np.sin(0.5 * np.pi * np.clip((t - t_on) / XFADE_S, 0, 1))
        down = np.cos(0.5 * np.pi * np.clip((t - (t_off - XFADE_S)) / XFADE_S, 0, 1))
        gain = up * down

        left = np.zeros_like(t)
        right = np.zeros_like(t)
        for j, f in enumerate(notes):
            pan = PAN[j % len(PAN)]
            for mult, w in ((1, 1.0), (2, 0.28)):          # core tone + quieter octave up
                fm = f * mult
                centre = np.sin(2 * np.pi * fm * t)
                low = np.sin(2 * np.pi * fm * (1 - DETUNE) * t)
                high = np.sin(2 * np.pi * fm * (1 + DETUNE) * t)
                left += w * (1 - pan) * (0.5 * centre + 0.25 * low)
                right += w * (1 + pan) * (0.5 * centre + 0.25 * high)
        root_tone = 0.2 * np.sin(2 * np.pi * root * t)     # very soft low root
        left += root_tone
        right += root_tone

        out[i0:i1, 0] += (gain * left).astype(np.float32)
        out[i0:i1, 1] += (gain * right).astype(np.float32)
        k += 1
    return out


# ---------------------------------------------------------------- effects

def peak_to(sig, peak):
    m = np.max(np.abs(sig))
    return sig * (peak / m) if m > 0 else sig


def add_at(fx, start_s, sig):
    """Add a mono effect to the stereo buffer (centre) starting at start_s seconds."""
    i0 = int(round(start_s * SR))
    a = max(0, -i0)
    b = min(len(sig), len(fx) - i0)
    if b > a:
        fx[i0 + a:i0 + b] += sig[a:b, None].astype(np.float32)


def whoosh(rng, dur=0.6, peak=0.045):
    """Filtered noise sweep: the band centre rises from ~330 Hz to ~2.6 kHz, then falls back."""
    n = int(dur * SR)
    nfft, hop = 1024, 256
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(nfft) / nfft)
    padded = np.concatenate([np.zeros(nfft), rng.standard_normal(n), np.zeros(nfft)])
    out = np.zeros_like(padded)
    norm = np.zeros_like(padded)
    logf = np.log2(np.maximum(np.fft.rfftfreq(nfft, 1 / SR), 20.0))
    for start in range(0, len(padded) - nfft + 1, hop):
        centre = (start + nfft / 2 - nfft) / n
        u = min(max(centre, 0.0), 1.0)
        fc = 330.0 * (2600.0 / 330.0) ** np.sin(np.pi * u)
        mask = np.exp(-0.5 * ((logf - np.log2(fc)) / 0.45) ** 2)
        frame = np.fft.irfft(np.fft.rfft(padded[start:start + nfft] * win) * mask, nfft)
        out[start:start + nfft] += frame * win
        norm[start:start + nfft] += win * win
    y = out[nfft:nfft + n] / np.maximum(norm[nfft:nfft + n], 1e-9)
    y *= np.sin(np.pi * np.arange(n) / n) ** 1.5          # swell in, fall away
    return peak_to(y, peak)


def tick(freq, last=False):
    n = int(0.08 * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * freq * t) * np.minimum(1, t / 0.002) * np.exp(-t / 0.012)
    return peak_to(y, 0.05 if last else 0.035)


def bell(freq, peak, decay=0.45, dur=1.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = (np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)
         + 0.15 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / (decay * 0.5))
         + 0.05 * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-t / (decay * 0.25)))
    y *= np.minimum(1, t / 0.004)
    return peak_to(y, peak)


def thud(peak=0.08):
    """Low sine, pitch drops from about 78 Hz to 50 Hz, quick decay."""
    n = int(0.7 * SR)
    t = np.arange(n) / SR
    f = 50 + 28 * np.exp(-t / 0.06)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, t / 0.004) * np.exp(-t / 0.16)
    return peak_to(y, peak)


def pop(peak=0.05):
    """Very short soft blip, pitch drops from about 260 Hz to 110 Hz."""
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    f = 110 + 150 * np.exp(-t / 0.015)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, t / 0.002) * np.exp(-t / 0.022)
    return peak_to(y, peak)


def build_effects(n, tl, rng):
    fx = np.zeros((n, 2), dtype=np.float32)
    beats, scenes = tl["beats"], tl["scenes"]
    counts = {"whoosh": 0, "pop": 0, "tick": 0, "chime": 0, "thud": 0, "sting": 0}

    for sc in scenes[1:]:                                   # every scene start except the first
        add_at(fx, sc["start"] - 0.2, whoosh(rng))
        counts["whoosh"] += 1

    pop_sig = pop()
    for bid, b in beats.items():
        tag = b.get("tag")
        if tag == "card":
            add_at(fx, b["start"], pop_sig)
            counts["pop"] += 1
        elif tag == "count":
            for k in range(4):                              # one tick per second, 4 in all
                add_at(fx, b["start"] + k, tick(1300.0 if k < 3 else 1500.0, last=(k == 3)))
                counts["tick"] += 1
            add_at(fx, b["end"], bell(1318.51, 0.05))       # E6 then B6: the reveal
            add_at(fx, b["end"] + 0.13, bell(1975.53, 0.05))
            counts["chime"] += 1

    thud_sig = thud()
    for bid in ("hook.4", "idea.12"):                       # the dramatic thuds

        if bid in beats:
            add_at(fx, beats[bid]["start"], thud_sig)
        counts["thud"] += 1

    for k, f in enumerate((523.25, 659.26, 783.99)):        # rising 3-note sting: C5, E5, G5

        if "outro.14" in beats:
            add_at(fx, beats["outro.14"]["start"] + 0.13 * k, bell(f, 0.05, decay=0.3, dur=0.7))
    counts["sting"] += 1
    return fx, counts


# ---------------------------------------------------------------- output

def loudnorm_two_pass(src, dst):
    err = ffmpeg("-i", str(src), "-af", f"loudnorm={LOUD}:print_format=json", "-f", "null", "-")
    measured = json.loads(re.findall(r"\{[^{}]*\}", err)[-1])
    chain = (f"loudnorm={LOUD}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
             f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
             f":offset={measured['target_offset']}:linear=true:print_format=json")
    err = ffmpeg("-i", str(src), "-af", chain, "-ar", str(SR), "-ac", "2",
                 "-c:a", "pcm_s16le", str(dst))
    mode = re.search(r'"normalization_type"\s*:\s*"(\w+)"', err)
    return measured, (mode.group(1) if mode else "unknown")


def finalize(src, dst, n):
    """Force the exact timeline length and write 16-bit stereo, 48 kHz."""
    data, sr = sf.read(src, dtype="int16", always_2d=True)
    assert sr == SR and data.shape[1] == 2, (sr, data.shape)
    if len(data) < n:
        data = np.pad(data, ((0, n - len(data)), (0, 0)))
    data = data[:n]
    sf.write(dst, data, SR, subtype="PCM_16")


def measure_lufs(path):
    err = ffmpeg("-i", str(path), "-af", "ebur128", "-f", "null", "-")
    return float(re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", err)[-1])


def report(name, path, n):
    info = sf.info(path)
    data, _ = sf.read(path, dtype="float32", always_2d=True)
    peak = float(np.max(np.abs(data)))
    print(f"{path.name}: {info.frames} frames = {info.frames / info.samplerate:.3f} s "
          f"(timeline {n / SR:.3f} s), {info.samplerate} Hz, {info.channels} ch, {info.subtype}")
    print(f"  peak {db(peak):.2f} dBFS, integrated loudness {measure_lufs(path):.2f} LUFS")
    assert info.frames == n and info.samplerate == SR and info.channels == 2
    assert peak < 1.0, f"{name} clips"


def main():
    tl = json.loads((BUILD / "timeline.json").read_text())
    dur = float(tl["duration"])
    n = int(round(dur * SR))
    env = np.asarray(tl["env"], dtype=np.float64)
    frame_t = (np.arange(len(env)) + 0.5) / FPS
    rng = np.random.default_rng(SEED)
    t = np.arange(n) / SR

    with tempfile.TemporaryDirectory(prefix="nightdesk-audio-") as tmp:
        tmp = Path(tmp)

        # 1. Voice, at 48 kHz, exactly the timeline length.
        voice = fit(load_narration_48k(tmp), n)
        voice_st = np.repeat(voice[:, None], 2, axis=1)
        is_speech = np.interp(t, frame_t, env) > 0.3
        speech_rms = rms(voice[is_speech])

        # 2. Music: pad at the speaking level, ducked by the voice envelope, faded in and out.
        speaking = speaking_curve(env)
        s_t = np.interp(t, frame_t, speaking)
        duck = 10 ** (SILENCE_RISE_DB * (1 - s_t) / 20)
        fade = np.sin(0.5 * np.pi * np.clip(t / 3, 0, 1)) * np.cos(0.5 * np.pi * np.clip((t - (dur - 4)) / 4, 0, 1))
        pad = pad_music(n)
        target = speech_rms * 10 ** (MUSIC_DB / 20)
        gain = (target / rms(pad)) * duck * fade
        music = pad * gain[:, None].astype(np.float32)
        del pad, duck, fade, gain, s_t

        # 3. Effects.
        fx, counts = build_effects(n, tl, rng)

        # 4. Mix, check the peak, and scale only if it would clip.
        full = voice_st + music + fx
        mix_peak = float(np.max(np.abs(full)))
        scaled = mix_peak > PEAK_LIMIT
        if scaled:
            full *= PEAK_LIMIT / mix_peak

        # 5. Music vs voice levels, from the separate stems (before loudnorm).
        w = slice(30 * SR, 60 * SR)
        m_win, v_win = music[w], voice_st[w]
        sp = is_speech[w]
        ratio_win = db(rms(m_win) / rms(v_win))
        ratio_speech = db(rms(m_win[sp]) / rms(v_win[sp]))

        full_f = tmp / "full_float.wav"
        voice_f = tmp / "voice_float.wav"
        sf.write(full_f, full, SR, subtype="FLOAT")
        sf.write(voice_f, voice_st, SR, subtype="FLOAT")
        del full, music, fx, m_win, v_win

        # 6. Two-pass loudnorm, then exact length and 16-bit output.
        print(f"Voice RMS while speaking: {db(speech_rms):.1f} dBFS")
        print(f"Effects: {counts['whoosh']} whooshes, {counts['pop']} pops, {counts['tick']} ticks, "
              f"{counts['chime']} chimes, {counts['thud']} thuds, {counts['sting']} sting")
        print(f"Mix peak before loudnorm: {db(mix_peak):.2f} dBFS"
              + (" (scaled down to avoid clipping)" if scaled else " (no scaling needed)"))
        for name, src, dst in (("full", full_f, BUILD / "audio_full.wav"),
                               ("voice", voice_f, BUILD / "audio_voice.wav")):
            measured, mode = loudnorm_two_pass(src, tmp / f"{name}_norm.wav")
            finalize(tmp / f"{name}_norm.wav", dst, n)
            print(f"loudnorm {name}: measured {float(measured['input_i']):.2f} LUFS, "
                  f"true peak {float(measured['input_tp']):.2f} dBTP, mode {mode}")

        print(f"Music vs voice, 30-60 s stretch: music {ratio_win:+.1f} dB "
              f"(speaking frames only: {ratio_speech:+.1f} dB)")

        for name, dst in (("full", BUILD / "audio_full.wav"), ("voice", BUILD / "audio_voice.wav")):
            report(name, dst, n)


if __name__ == "__main__":
    main()
