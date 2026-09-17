#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

say() {
  printf '[%s] [launcher] %s\n' "$(date '+%H:%M:%S')" "$*"
}

say "Read-the-Room strengthening suite launcher"
say "Working directory: $(pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN was not found in PATH." >&2
  exit 2
fi
say "Base Python: $($PYTHON_BIN --version 2>&1)"

if [[ ! -x .venv/bin/python ]]; then
  say "Creating virtual environment .venv ..."
  "$PYTHON_BIN" -m venv .venv
else
  say "Virtual environment already exists."
fi

STAMP=.venv/.readroom_requirements_ready
if [[ ! -f "$STAMP" || requirements.txt -nt "$STAMP" ]]; then
  say "Installing/updating Python dependencies (output is intentionally visible) ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch "$STAMP"
else
  say "Dependencies already installed; skipping pip. Delete $STAMP to force reinstall."
fi

if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  say "OPENROUTER_API_KEY is present in the environment."
elif [[ -f .env ]] && grep -Eq '^OPENROUTER_API_KEY=' .env; then
  say "OPENROUTER_API_KEY appears to be present in .env."
else
  say "WARNING: OPENROUTER_API_KEY not detected in environment or .env; live LLM stage will stop unless --offline is used."
fi

say "Starting Python experiment runner with unbuffered output ..."
exec env PYTHONUNBUFFERED=1 .venv/bin/python -u readroom_strengthen.py "$@"
