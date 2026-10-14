# Build the clean explainer video

This optional toolchain creates a public educational video from narration and diagrams. It never reads the app's practice account, logs, hardware inventory or private screenshots. The previously generated developer videos are intentionally not included.

The app works without any video dependencies.

## Prepare the optional tools

From the repository root, use Python 3.11–3.13:

```bash
python3 -m venv video/.venv
video/.venv/bin/python -m pip install -r requirements-video.txt
npm ci
npx playwright install chromium
```

Install FFmpeg with FFprobe (for example `brew install ffmpeg` on a Mac). The scripts find them on PATH, or use the `FFMPEG` and `FFPROBE` environment variables.

## Build

```bash
video/.venv/bin/python video/build_video.py
```

If the public voice files are missing, this asks whether to download about 354 MB. Noninteractive users may explicitly consent with `--download-models`. The download uses the upstream [Kokoro ONNX model-files-v1.1 release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1); both assets are checked against pinned SHA-256 digests. Review the upstream model and package licenses before distributing generated speech.

The command creates narration, captions, chapter data, preview stills, diagrams, audio and MP4s, then a local player with chapters, speed control and resume. `--preview` stops after preview stills. Inspect previews before sharing generated media.

Outputs stay ignored in `video/build/`, `video/models/` and the generated root MP4/player files. Do not add generated files to source Git history. The committed video source is shareable; generated media is a separate local output.

The script sources live in `video/src/`. Edit `make_script.py` for narration and `scenes.js` for visual cards. The animation engine resolves every element from the narration timeline, so changing a line moves the matching visuals.
