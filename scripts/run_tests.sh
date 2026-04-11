#!/usr/bin/env bash

# TeamAgent Test Runner Script
# Supports:
#   1. Fast parallel tests (default)
#   2. Coverage reports (--cov)
#   3. Arbitrary pytest passthrough arguments

if [ -x ".venv/bin/pytest" ]; then
  PYTEST_BIN=".venv/bin/pytest"
else
  PYTEST_BIN="pytest"
fi

# Default options (Fast Mode)
OPTS=("-n" "auto" "--dist" "loadscope")
COV_OPTS=()
PASSTHROUGH=()

# Parse arguments
for arg in "$@"; do
  case $arg in
    --cov)
      # Enable coverage for core source and TUI
      COV_OPTS=("--cov=src" "--cov=tui" "--cov-report=term-missing" "--cov-report=xml:test_data/coverage/coverage.xml")
      ;;
    --serial)
      # Run tests serially (no xdist)
      OPTS=()
      ;;
    *)
      PASSTHROUGH+=("$arg")
      ;;
  esac
done

# If no specific files/dirs are provided, default to unit and integration tests
if [ ${#PASSTHROUGH[@]} -eq 0 ]; then
  PASSTHROUGH=("tests/unit" "tests/integration")
fi

echo "🚀 Running tests with: ${PYTEST_BIN} ${OPTS[*]} ${COV_OPTS[*]} ${PASSTHROUGH[*]}"

# Run pytest
"${PYTEST_BIN}" "${OPTS[@]}" "${COV_OPTS[@]}" "${PASSTHROUGH[@]}"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "✅ Tests passed!"
else
  echo "❌ Tests failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
