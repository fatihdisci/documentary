#!/usr/bin/env bash
# Double-click this file in Finder to run Extinct Video Builder in production.
#
# Unlike "Launch EVB.command", this builds the frontend once and serves the
# whole app from a single process on http://127.0.0.1:8756 — no Vite dev
# server. It's the fastest way to just *use* the app.
#
# What it does:
#   1. Runs ./dev.sh --setup if the backend venv doesn't exist yet.
#   2. Builds the frontend (frontend/dist) if it hasn't been built.
#   3. Starts the single production server and opens the app in your browser.
#   4. Closing this Terminal window stops the server.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\033[36m[evb]\033[0m %s\n' "$*"; }

clear
echo "======================================"
echo " Extinct Video Builder (production)"
echo "======================================"
echo

if [[ ! -x "backend/.venv/bin/python" ]] || [[ ! -d "frontend/node_modules" ]]; then
  log "First run detected — installing dependencies (this can take a few minutes)..."
  ./dev.sh --setup
  echo
fi

log "Building and starting the app on http://127.0.0.1:8756 ..."
log "Keep this window open while you use the app; close it (or Ctrl-C) to stop."
echo

# prod.sh builds if needed, opens the browser, and serves until interrupted.
exec ./prod.sh
