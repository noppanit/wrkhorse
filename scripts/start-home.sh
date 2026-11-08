#!/usr/bin/env bash
# Start a local-only wrkhorse MCP server and local Ollama worker on macOS.
# Run as: bash scripts/start-home.sh [--check]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed. Install from https://ollama.com/download/mac" >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1 || ! curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "Ollama is not serving on localhost. Launch the Ollama Mac app first." >&2
  exit 2
fi

export WRKHORSE_INFERENCE_MODEL="${WRKHORSE_INFERENCE_MODEL:-qwen2.5-coder:1.5b}"
if [[ "$WRKHORSE_INFERENCE_MODEL" == *:cloud ]]; then
  echo "Refusing cloud Ollama tags: this deployment must run on the Mac." >&2
  exit 2
fi
if ! ollama show "$WRKHORSE_INFERENCE_MODEL" >/dev/null 2>&1; then
  echo "Missing local model. Run: ollama pull $WRKHORSE_INFERENCE_MODEL" >&2
  exit 2
fi

PYTHON_BIN="${WRKHORSE_PYTHON:-python3.12}"
if [[ ! -d .venv ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python 3.12 required for the pinned dependencies. Install it, then retry." >&2
    exit 2
  fi
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c 'import mcp, httpx2' >/dev/null 2>&1; then
  echo "Installing pinned dependencies in local .venv ..."
  python -m pip install -r requirements.txt
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
WRKHORSE_AUTH_TOKEN="$(cat "$TOKEN_FILE")"

# Force local-only model and gateway binding, irrespective of ambient variables.
export WRKHORSE_HOST=127.0.0.1
export WRKHORSE_PORT="${WRKHORSE_PORT:-8765}"
export WRKHORSE_DB="${WRKHORSE_DB:-$ROOT/var/home.sqlite}"
export WRKHORSE_EXECUTION_MODE=inference-only
export WRKHORSE_INFERENCE_BASE_URL=http://127.0.0.1:11434/v1
export WRKHORSE_INFERENCE_API_KEY=ollama-local
export WRKHORSE_INFERENCE_TIMEOUT_SECONDS="${WRKHORSE_INFERENCE_TIMEOUT_SECONDS:-120}"
export WRKHORSE_RATE_LIMIT_PER_MINUTE="${WRKHORSE_RATE_LIMIT_PER_MINUTE:-30}"

if [[ "${1:-}" == "--check" ]]; then
  python -m unittest discover -s tests -p test_home_deployment.py -v
  echo "Preflight passed for $WRKHORSE_INFERENCE_MODEL. This did not invoke the model or expose a tunnel."
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown option. Usage: bash scripts/start-home.sh [--check]" >&2
  exit 2
fi

echo "Starting home wrkhorse on http://127.0.0.1:$WRKHORSE_PORT/mcp"
echo "Model: $WRKHORSE_INFERENCE_MODEL (local-only)"
echo "Your token is stored in var/mcp-token; do not paste it into public chats or GitHub."
if [[ -n "${WRKHORSE_PUBLIC_HOST:-}" ]]; then
  echo "Allowing tunnel Host: $WRKHORSE_PUBLIC_HOST (requires separate authenticated tunnel setup)"
else
  echo "No public tunnel hostname configured. Loopback-only MCP calls are supported."
fi
echo "Press Ctrl-C to stop the worker and gateway."
python -m wrkhorse worker &
WORKER_PID=$!
cleanup() {
  kill "$WORKER_PID" >/dev/null 2>&1 || true
  wait "$WORKER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
python -m wrkhorse serve
