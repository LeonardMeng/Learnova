#!/usr/bin/env bash
# Start Learnova's API and web app together. Ctrl+C stops both.
#
#   scripts/dev.sh           API on http://127.0.0.1:8000 + web app dev server (live reload)
#                            on http://localhost:5173  <- open this one
#   scripts/dev.sh --built   build the web app once, then serve everything from the API
#                            on http://127.0.0.1:8000 (no Node process left running)
#
# Secrets come from .env in the project folder, as with `uv run --env-file .env learnova ...`.
# Works with macOS's built-in bash 3.2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
API_PORT=8000  # frontend/vite.config.ts forwards /api here
WEB_PORT=5173

need() { command -v "$1" >/dev/null 2>&1 || { echo "$2" >&2; exit 1; }; }
need uv "uv is not installed: see https://docs.astral.sh/uv/"
need npm "Node.js is not installed: the web app needs Node 20 or newer."

port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}
for port in $API_PORT $([ "${1:-}" = "--built" ] || echo $WEB_PORT); do
  if port_busy "$port"; then
    echo "Port $port is already in use (is Learnova already running?)." >&2
    exit 1
  fi
done

ENV_FILE=()
if [ -f .env ]; then
  ENV_FILE=(--env-file .env)
else
  echo "Note: no .env file; Notion and model keys must come from your shell." >&2
fi

if [ ! -d frontend/node_modules ]; then
  echo "Installing the web app's packages (first run)…"
  (cd frontend && npm ci --no-fund --no-audit)
fi

if [ "${1:-}" = "--built" ]; then
  (cd frontend && npm run build)
  echo "Open http://127.0.0.1:$API_PORT"
  exec uv run ${ENV_FILE[@]+"${ENV_FILE[@]}"} learnova serve --port "$API_PORT"
fi

# Run each server in its own process group, so stopping it also stops the processes it starts.
set -m
uv run ${ENV_FILE[@]+"${ENV_FILE[@]}"} learnova serve --port "$API_PORT" &
API_PID=$!
(cd frontend && exec npm run dev -- --port "$WEB_PORT" --strictPort) &
WEB_PID=$!
set +m

stop() {
  trap - INT TERM EXIT
  kill -TERM -- "-$API_PID" "-$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

echo "API      http://127.0.0.1:$API_PORT   (docs at /docs)"
echo "Web app  http://localhost:$WEB_PORT   <- open this"
echo "Ctrl+C stops both."

# If either one stops (or fails to start), stop the other too.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 1
done
echo "One of the servers stopped; stopping the other." >&2
