#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Open Install.command first to install Nightdesk on this computer."
  if [[ -t 0 ]]; then read -r -p "Press Return to close this window. " || true; fi
  exit 1
fi
"$ROOT/.venv/bin/python" "$ROOT/scripts/launch.py" "$@"
