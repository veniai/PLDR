#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
VENV_DIR="${PLDR_VENV_DIR:-.venv}"
if [[ -x "$VENV_DIR/bin/python" ]]; then PYTHON_BIN="$VENV_DIR/bin/python"; else PYTHON_BIN="${PYTHON_BIN:-python3}"; fi
POLL_SECONDS="${PLDR_COLLECTION_POLL_SECONDS:-2}"
export PYTHONPATH="$PWD/services/intel-api${PYTHONPATH:+:$PYTHONPATH}"
echo "PLDR P1 collector: durable single worker (poll ${POLL_SECONDS}s)"
exec "$PYTHON_BIN" -m pldr_api.collector --loop --poll-seconds "$POLL_SECONDS" "$@"
