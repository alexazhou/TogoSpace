#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$REPO_ROOT/.venv/bin:$PATH"
exec python "$REPO_ROOT/src/db.py" "$@"
