#!/usr/bin/env bash
# Start the backend and frontend together for development.
#
#   ./dev.sh              backend on :8756, Vite on :5173
#   ./dev.sh --setup      create the venv and install everything first
#
# Ctrl-C stops both.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
PYTHON_BIN="${EVB_PYTHON:-python3.11}"

log() { printf '\033[36m[evb]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[evb]\033[0m %s\n' "$*" >&2; }

setup() {
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    err "$PYTHON_BIN not found. Install it (brew install python@3.11) or set EVB_PYTHON."
    exit 1
  fi
  log "creating venv with $PYTHON_BIN"
  "$PYTHON_BIN" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  log "installing backend dependencies"
  "$VENV/bin/python" -m pip install -r "$BACKEND/requirements.txt"
  log "installing frontend dependencies"
  (cd "$FRONTEND" && npm install)
  log "setup complete"
}

if [[ "${1:-}" == "--setup" ]]; then
  setup
  exit 0
fi

# --- Preflight -------------------------------------------------------------

if [[ ! -x "$VENV/bin/python" ]]; then
  err "No virtualenv at $VENV. Run './dev.sh --setup' first."
  exit 1
fi

if [[ ! -d "$FRONTEND/node_modules" ]]; then
  err "Frontend dependencies are missing. Run './dev.sh --setup' first."
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/ffmpeg ]]; then
  err "FFmpeg was not found on PATH. Install it with: brew install ffmpeg"
  err "(You can also set an explicit path later on the Settings page.)"
fi

# --- Run -------------------------------------------------------------------

PIDS=()
cleanup() {
  log "shutting down"
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "backend  → http://127.0.0.1:8756"
(cd "$BACKEND" && exec "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8756 --reload) &
PIDS+=($!)

log "frontend → http://localhost:5173"
(cd "$FRONTEND" && exec npm run dev) &
PIDS+=($!)

wait
