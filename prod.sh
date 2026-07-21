#!/usr/bin/env bash
# Build and run Extinct Video Builder as a single production server.
#
#   ./prod.sh            build the frontend (if needed) and serve everything
#                        from one process on http://127.0.0.1:8756
#   ./prod.sh --build    force a fresh frontend build, then serve
#   ./prod.sh --no-open  don't open a browser
#
# Unlike ./dev.sh there is no Vite dev server and no :5173 — the backend serves
# the built frontend (frontend/dist) and the API from the same origin, so there
# is nothing to proxy and the app works offline once built. Ctrl-C stops it.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
DIST="$FRONTEND/dist"
HOST="${EVB_HOST:-127.0.0.1}"
PORT="${EVB_PORT:-8756}"
URL="http://$HOST:$PORT"

log() { printf '\033[36m[evb]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[evb]\033[0m %s\n' "$*" >&2; }

FORCE_BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build) FORCE_BUILD=1 ;;
    --no-open) OPEN=0 ;;
    *) err "unknown option: $arg"; exit 1 ;;
  esac
done

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

# --- Build -----------------------------------------------------------------

if [[ "$FORCE_BUILD" == "1" || ! -f "$DIST/index.html" ]]; then
  log "building the frontend (this bakes it into $DIST)"
  (cd "$FRONTEND" && npm run build)
else
  log "using the existing build in $DIST (pass --build to rebuild)"
fi

# --- Run -------------------------------------------------------------------

open_browser() {
  # Wait for the server to answer, then open the default browser.
  for _ in $(seq 1 40); do
    if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then open "$URL"
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
      fi
      return
    fi
    sleep 0.25
  done
}

if [[ "$OPEN" == "1" ]]; then open_browser & fi

log "serving on $URL  (API and app share this one origin)"
log "press Ctrl-C to stop"
cd "$BACKEND"
exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
