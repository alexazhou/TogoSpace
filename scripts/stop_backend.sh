#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/run/backend.pid"
[ -f "$PID_FILE" ] || { echo "后端未运行" >&2; exit 1; }
PID=$(cat "$PID_FILE")
kill "$PID" && echo "后端已停止 (PID $PID)"
