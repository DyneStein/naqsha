#!/usr/bin/env bash
# Naqsha. ./run.sh               -> web app on http://127.0.0.1:8765
#          ./run.sh process DIR   -> videos in DIR to a finished map, no clicks
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/dist ] || [ -n "$(find frontend/src -newer frontend/dist/index.html -print -quit)" ]; then
  (cd frontend && { [ -d node_modules ] || npm install; } && npm run build)
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running; start Docker Desktop (needed for stitching)." >&2
fi
cd backend
if [ "${1:-}" = "process" ]; then
  # unattended: ./run.sh process "/path/to/videos" [--quality standard] [--out DIR]
  shift
  exec .venv/bin/python -m naqsha process "$@"
fi
exec .venv/bin/python -m naqsha serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8765}"
