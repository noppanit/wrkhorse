#!/usr/bin/env bash
# Start a local-only wrkhorse MCP server and local-model worker.
# Run as: bash scripts/start-home.sh [--check]
#
# Written for macOS's stock bash 3.2 (no `wait -n`, no associative arrays).
# Also runs on Linux. Settings may come from the environment or from a
# gitignored .env file in the repo root (see .env.example).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check" ) ]]; then
  echo "Unknown option. Usage: bash scripts/start-home.sh [--check]" >&2
  exit 2
fi

# Optional owner settings (model tag, model URL, Access values). Values set in
# the environment win over .env. Forced safety settings are applied below.
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    if [[ "$line" =~ ^[[:space:]]*(WRKHORSE_[A-Z0-9_]+)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
      # Never take the gateway token from .env: it lives in var/mcp-token.
      [[ "$key" == "WRKHORSE_AUTH_TOKEN" ]] && continue
      if [[ -z "${!key:-}" ]]; then
        export "$key=$value"
      fi
    fi
  done < "$ROOT/.env"
fi

# Pick a Python the pinned wheels support (3.11-3.13 all tested in CI-like runs).
pick_python() {
  if [[ -n "${WRKHORSE_PYTHON:-}" ]]; then
    echo "$WRKHORSE_PYTHON"; return 0
  fi
  for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  if ! PYTHON_BIN="$(pick_python)"; then
    echo "Python 3.11-3.13 is required (e.g. brew install python@3.12)." >&2
    exit 2
  fi
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c 'import mcp, httpx2, jwt' >/dev/null 2>&1; then
  echo "Installing pinned dependencies in local .venv ..."
  python -m pip install -r requirements.txt
fi

export WRKHORSE_INFERENCE_MODEL="${WRKHORSE_INFERENCE_MODEL:-qwen2.5-coder:1.5b}"
# Default: Ollama on this machine. To use a model on another machine you own,
# set WRKHORSE_INFERENCE_BASE_URL=http://<private-ip-or-host.local>:11434/v1
# and WRKHORSE_ALLOW_LAN_MODEL=1. Cloud endpoints and cloud tags are refused.
export WRKHORSE_INFERENCE_BASE_URL="${WRKHORSE_INFERENCE_BASE_URL:-http://127.0.0.1:11434/v1}"

# Reachability + model presence (+ warm-up unless --check). Same URL policy
# as the gateway and worker. Replaces the old `ollama` CLI checks so the
# model can live in LM Studio/llama.cpp or on another machine.
if [[ "${1:-}" == "--check" ]]; then
  python -m wrkhorse doctor
else
  echo "Checking and warming the model (first load can take a few minutes) ..."
  python -m wrkhorse doctor --warm
fi

mkdir -p var
TOKEN_FILE="$ROOT/var/mcp-token"
if [[ ! -f "$TOKEN_FILE" ]]; then
  python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "Generated a persistent MCP bearer token in var/mcp-token (gitignored)."
fi
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Cannot read var/mcp-token." >&2
  exit 2
fi
export WRKHORSE_AUTH_TOKEN
WRKHORSE_AUTH_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"

# Force a loopback gateway irrespective of ambient variables. The model URL
# was validated above; the gateway and worker validate it again.
export WRKHORSE_HOST=127.0.0.1
export WRKHORSE_PORT="${WRKHORSE_PORT:-8765}"
export WRKHORSE_DB="${WRKHORSE_DB:-$ROOT/var/home.sqlite}"
export WRKHORSE_EXECUTION_MODE=inference-only
# Owner-triggered startup enables text-only local-model questions.
export WRKHORSE_MODEL_CHAT_ENABLED=1
export WRKHORSE_INFERENCE_API_KEY=ollama-local
export WRKHORSE_INFERENCE_TIMEOUT_SECONDS="${WRKHORSE_INFERENCE_TIMEOUT_SECONDS:-180}"
export WRKHORSE_MODEL_CHAT_TIMEOUT_SECONDS="${WRKHORSE_MODEL_CHAT_TIMEOUT_SECONDS:-120}"
export WRKHORSE_RATE_LIMIT_PER_MINUTE="${WRKHORSE_RATE_LIMIT_PER_MINUTE:-30}"

if [[ "${1:-}" == "--check" ]]; then
  python -m unittest discover -s tests -p test_home_deployment.py -v
  echo "Preflight passed for $WRKHORSE_INFERENCE_MODEL. This did not load the model or expose a tunnel."
  exit 0
fi

if (exec 3<>"/dev/tcp/127.0.0.1/$WRKHORSE_PORT") 2>/dev/null; then
  echo "Port $WRKHORSE_PORT is already in use (another wrkhorse?). Stop it or set WRKHORSE_PORT." >&2
  exit 2
fi

echo "Starting home wrkhorse on http://127.0.0.1:$WRKHORSE_PORT/mcp"
echo "Model: $WRKHORSE_INFERENCE_MODEL at $WRKHORSE_INFERENCE_BASE_URL"
echo "Your token is stored in var/mcp-token; do not paste it into public chats or GitHub."
if [[ -n "${WRKHORSE_PUBLIC_HOST:-}" ]]; then
  echo "Allowing tunnel Host: $WRKHORSE_PUBLIC_HOST (requires separate authenticated tunnel setup)"
else
  echo "No public tunnel hostname configured. Loopback-only MCP calls are supported."
fi

python -m wrkhorse worker &
WORKER_PID=$!
CAFFEINATE_PID=""
cleanup() {
  kill "$WORKER_PID" >/dev/null 2>&1 || true
  wait "$WORKER_PID" >/dev/null 2>&1 || true
  if [[ -n "$CAFFEINATE_PID" ]]; then
    kill "$CAFFEINATE_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Fail fast if the worker died on a config error: otherwise the gateway would
# happily queue jobs that nothing will ever run.
sleep 2
if ! kill -0 "$WORKER_PID" >/dev/null 2>&1; then
  echo "The worker exited during startup; see the error above." >&2
  exit 2
fi

# macOS: keep the Mac from idle-sleeping while the server runs. This does NOT
# stop sleep when the lid is closed on battery. Opt out: WRKHORSE_NO_CAFFEINATE=1.
if [[ "${WRKHORSE_NO_CAFFEINATE:-}" != "1" ]] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w $$ &
  CAFFEINATE_PID=$!
  echo "Idle sleep is blocked while this runs (caffeinate)."
fi

echo "Press Ctrl-C to stop the worker and gateway."
python -m wrkhorse serve
