#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs .runtime
exec > >(tee -a logs/setup.log) 2>&1
trap 'echo "Setup/startup stopped. See logs/setup.log. Your data has not been deleted."' ERR
command -v python3 >/dev/null || { echo "Install Python 3.11 or newer first."; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer is required"'
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.runtime/browsers"
export PIP_CACHE_DIR="$ROOT/.runtime/cache"
PYTHON="$ROOT/.runtime/venv/bin/python"
if [ ! -x "$PYTHON" ]; then python3 -m venv .runtime/venv; fi
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m playwright install chromium
"$PYTHON" scripts/run_app.py --check
if [ "${1:-}" != "--setup-only" ]; then "$PYTHON" scripts/run_app.py; fi
