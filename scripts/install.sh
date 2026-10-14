#!/usr/bin/env bash
# Install only the app's software. Data and models are set up later by the user.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NATIVE=1
if [[ "${1:-}" == "--no-native" ]]; then NATIVE=0; shift; fi
if [[ $# -ne 0 ]]; then echo "Usage: scripts/install.sh [--no-native]" >&2; exit 2; fi

fail() { echo "Nightdesk installation stopped: $*" >&2; exit 1; }
trap 'echo "The command above failed. Fix its reported problem, then run Install.command again." >&2' ERR

supports_python() {
  "$1" -c 'import sys; raise SystemExit(not ((3,11) <= sys.version_info[:2] < (3,14)))' 2>/dev/null
}

PYTHON=""
if [[ -n "${NIGHTDESK_PYTHON:-}" ]]; then
  supports_python "$NIGHTDESK_PYTHON" || fail "NIGHTDESK_PYTHON must point to Python 3.11, 3.12, or 3.13."
  PYTHON="$NIGHTDESK_PYTHON"
else
  for candidate in python3.13 python3.12 python3.11 python3; do
    resolved="$(command -v "$candidate" || true)"
    if [[ -n "$resolved" ]] && supports_python "$resolved"; then PYTHON="$resolved"; break; fi
  done
fi
[[ -n "$PYTHON" ]] || fail "Install Python 3.11, 3.12, or 3.13 from https://www.python.org/downloads/ (or brew install python@3.11), then run Install.command again."
[[ -w "$ROOT" ]] || fail "This folder is read-only. Move the extracted Nightdesk folder to a folder you can write to, then retry."

PYTHON_VERSION="$("$PYTHON" -c 'import platform; print(platform.python_version())')"
echo "Installing Nightdesk with $PYTHON_VERSION."
if [[ -e "$ROOT/.venv" && ! -x "$ROOT/.venv/bin/python" ]]; then
  fail "The .venv folder is incomplete. Rename it, then run Install.command again."
fi
if [[ ! -e "$ROOT/.venv" ]]; then "$PYTHON" -m venv "$ROOT/.venv"; fi
VENV_PYTHON="$ROOT/.venv/bin/python"
supports_python "$VENV_PYTHON" || fail "The existing .venv uses an unsupported Python. Rename .venv, then retry."

# Do not silently compile a scientific dependency on the user's computer.
"$VENV_PYTHON" -m pip install --upgrade "pip>=24,<27"
"$VENV_PYTHON" -m pip install --only-binary=numpy,pandas,scipy,scikit-learn,lightgbm,pyarrow,cffi,lxml,curl_cffi,pydantic_core -r "$ROOT/requirements.txt"
"$VENV_PYTHON" -m pip install --no-deps -e "$ROOT"
"$VENV_PYTHON" "$ROOT/scripts/doctor.py"

if [[ $NATIVE -eq 1 && "$(uname -s)" == "Darwin" ]]; then
  if command -v swiftc >/dev/null 2>&1 && xcrun --find swiftc >/dev/null 2>&1; then
    if ! bash "$ROOT/shell/build.sh"; then
      touch "$ROOT/.native-disabled"
      echo "The optional Mac window could not be built. Launch.command will open Nightdesk in your browser."
    fi
  else
    touch "$ROOT/.native-disabled"
    echo "Apple command line tools are optional. Launch.command will open Nightdesk in your browser."
    echo "For a Mac window later, run xcode-select --install and then shell/build.sh."
  fi
fi
echo "Installed. Open Launch.command next. Its setup screen lets you download your own prices and train your own model."
