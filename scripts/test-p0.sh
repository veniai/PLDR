#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
VENV_DIR="${PLDR_VENV_DIR:-.venv}"
if [[ -x "$VENV_DIR/bin/python" ]]; then PYTHON_BIN="$VENV_DIR/bin/python"; else PYTHON_BIN="${PYTHON_BIN:-python3}"; fi
export PYTHONPATH="$PWD/services/intel-api${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py' -v
"$PYTHON_BIN" -m compileall -q services/intel-api/pldr_api
node --check apps/dashboard/assets/app.js
echo "P0 验收测试通过。"
