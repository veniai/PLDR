#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${PLDR_HOST:-127.0.0.1}"
PORT="${PLDR_PORT:-8765}"
VENV_DIR="${PLDR_VENV_DIR:-.venv}"
if [[ -x "$VENV_DIR/bin/python" ]]; then PYTHON_BIN="$VENV_DIR/bin/python"; else PYTHON_BIN="${PYTHON_BIN:-python3}"; fi
echo "PLDR P0: http://${HOST}:${PORT}"
exec "$PYTHON_BIN" -m uvicorn pldr_api.main:app --app-dir services/intel-api --host "$HOST" --port "$PORT" "$@"
