#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/run/tui.pid"
[ -f "$PID_FILE" ] || { echo "TUI 未运行" >&2; exit 1; }
PID=$(cat "$PID_FILE")
kill "$PID" && echo "TUI 已停止 (PID $PID)"
