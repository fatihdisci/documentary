#!/usr/bin/env bash
# Double-click this file in Finder to start Extinct Video Builder.
#
# First run: installs the backend venv and frontend dependencies (a few
# minutes, one-time). Every run after that starts in a few seconds.
#
# What it does:
#   1. Runs ./dev.sh --setup if the backend venv doesn't exist yet.
#   2. Starts the backend (:8756) and frontend (:5173) via ./dev.sh.
#   3. Waits for the backend to answer, then opens the app in your browser.
#   4. Closing this Terminal window stops both servers.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\033[36m[evb]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[evb]\033[0m %s\n' "$*" >&2; }

clear
echo "======================================"
echo " Extinct Video Builder"
echo "======================================"
echo

if [[ ! -x "backend/.venv/bin/python" ]] || [[ ! -d "frontend/node_modules" ]]; then
  log "First run detected — installing dependencies (this can take a few minutes)..."
  ./dev.sh --setup
  echo
fi

log "Starting backend and frontend..."
./dev.sh &
DEV_PID=$!

# Stop dev.sh (and everything it started) when this window closes.
trap 'log "Shutting down..."; kill "$DEV_PID" 2>/dev/null; wait 2>/dev/null' EXIT INT TERM

log "Waiting for the backend to come up..."
for _ in $(seq 1 60); do
  if curl -s -o /dev/null --max-time 1 http://127.0.0.1:8756/api/health; then
    break
  fi
  sleep 1
done

if curl -s -o /dev/null --max-time 1 http://127.0.0.1:8756/api/health; then
  log "Opening the app in your browser..."
  open "http://localhost:5173"
else
  err "The backend did not come up in time. Check the log output above."
  err "You can still try opening http://localhost:5173 manually."
fi

echo
log "Extinct Video Builder is running."
log "Keep this window open while you use the app."
log "Close this window (or press Ctrl-C) to stop it."
echo

wait "$DEV_PID"
