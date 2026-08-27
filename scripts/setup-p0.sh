#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${PLDR_VENV_DIR:-.venv}"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r services/intel-api/requirements.txt
echo "P0 运行环境已创建：$VENV_DIR"
echo "启动：./scripts/run-p0.sh"
