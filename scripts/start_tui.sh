#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python "$ROOT/tui/tui_main.py" "$@"
