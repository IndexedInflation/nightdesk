#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/scripts/install.sh" "$@"
if [[ -t 0 && $# -eq 0 ]]; then read -r -p "Press Return to close this window. " || true; fi
