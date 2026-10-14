"""Build a clean local explainer; optional voice assets are downloaded only with consent."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def run(*args):
    subprocess.run(args, cwd=HERE, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", help="consent to the optional voice asset downloads")
    parser.add_argument("--preview", action="store_true", help="make preview stills only after recording narration")
    args = parser.parse_args()
    missing = [name for name in ["kokoro_onnx", "soundfile", "numpy"] if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit("Install the optional video dependencies first: python -m pip install -r requirements-video.txt")
    for tool in ["node", os.environ.get("FFMPEG", "ffmpeg")]:
        if not shutil.which(tool):
            raise SystemExit(f"Missing {tool}. See video/README.md for installation steps.")
    if not (ROOT / "node_modules/playwright").is_dir():
        raise SystemExit("Install the optional browser tools first: npm ci && npx playwright install chromium")
    command = [sys.executable, str(HERE / "download_models.py")]
    if args.download_models:
        command.append("--yes")
    run(*command)
    run(sys.executable, "src/make_script.py")
    run(sys.executable, "-I", "src/tts.py", "--model", "models/kokoro-v1.0.onnx", "--voices", "models/voices-v1.0.bin")
    run(sys.executable, "src/make_data.py")
    run("node", "src/preview.mjs", "all")
    if not args.preview:
        run("node", "src/render.mjs")
        run(sys.executable, "src/audio.py")
        run(sys.executable, "src/mux.py")
        run(sys.executable, "src/make_player.py")
    print("Generated local media. Keep generated assets out of the source repository.")

if __name__ == "__main__":
    main()
