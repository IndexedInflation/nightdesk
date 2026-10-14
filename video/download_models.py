"""Download optional public Kokoro voice assets with pinned SHA-256 verification."""
import argparse
import hashlib
import os
from pathlib import Path
import urllib.request

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
ASSETS = {
    "kokoro-v1.0.onnx": (325505369, "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    "voices-v1.0.bin": (28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}

def valid(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest() == digest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="agree to download about 354 MB from the public upstream release")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent / "models"
    missing = [name for name, (size, digest) in ASSETS.items() if not valid(directory / name, size, digest)]
    if not missing:
        print("Voice assets already verified.")
        return
    if not args.yes:
        try:
            answer = input("Optional video voice assets are missing. Download about 354 MB from the public Kokoro release? [y/N] ")
        except EOFError:
            answer = ""
        if answer.lower() not in ("y", "yes"):
            raise SystemExit("Download skipped. Run again with --yes when you choose to download.")
    directory.mkdir(parents=True, exist_ok=True)
    for name in missing:
        size, digest = ASSETS[name]
        destination = directory / name
        temporary = destination.with_suffix(destination.suffix + ".partial")
        try:
            request = urllib.request.Request(BASE + name, headers={"User-Agent": "Nightdesk-video-builder"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    if handle.tell() > size:
                        raise ValueError("Download exceeds the expected asset size")
            if not valid(temporary, size, digest):
                raise ValueError("Asset checksum verification failed")
            os.replace(temporary, destination)
            print(f"Verified {name}")
        finally:
            temporary.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
