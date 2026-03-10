#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/src"
nohup python main.py "$@" >> "$ROOT/logs/backend_stdout.log" 2>&1 &
echo "后端已启动 (PID $!)"
