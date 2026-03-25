#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$REPO_ROOT/.venv/bin:$PATH"
cd "$REPO_ROOT/src"
nohup python backend_main.py "$@" >> "$REPO_ROOT/logs/backend_stdout.log" 2>&1 &
echo "后端已启动 (PID $!)"
