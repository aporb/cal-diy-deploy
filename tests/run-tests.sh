#!/usr/bin/env bash
# Runs every patch test against a patched cal.diy source tree.
#
#   CALDIY_SRC=/path/to/patched/src bash tests/run-tests.sh
#
# Exits non-zero if any suite fails, so it can gate a release.
set -uo pipefail

ROOT="${CALDIY_SRC:-/tmp/e2e/src}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$ROOT" ]; then
  echo "source root not found: $ROOT"
  echo "set CALDIY_SRC to a tree with the patches applied"
  exit 2
fi

echo "Testing patched source: $ROOT"
echo

failed=0
# Type-check first: the image build runs `yarn workspace @calcom/trpc run build`, which
# type-checks these files with an ES5 target. A type error there fails the whole build.
echo "==================================================================="
echo "  typecheck-patched.sh  (ES5 target, matches the trpc build)"
echo "==================================================================="
if bash "$HERE/typecheck-patched.sh" "$ROOT"; then
  echo "  -> typecheck PASSED"
else
  echo "  -> typecheck FAILED"
  failed=$((failed + 1))
fi
echo

for suite in verify-ast.mjs test-retry.mjs test-reminder.mjs test-alert.mjs; do
  echo "==================================================================="
  echo "  $suite"
  echo "==================================================================="
  if CALDIY_SRC="$ROOT" node "$HERE/$suite"; then
    echo "  -> $suite PASSED"
  else
    echo "  -> $suite FAILED"
    failed=$((failed + 1))
  fi
  echo
done

if [ "$failed" -ne 0 ]; then
  echo "RESULT: $failed suite(s) failed"
  exit 1
fi
echo "RESULT: all suites passed"
