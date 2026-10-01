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

# node_modules is gitignored, so a fresh clone will not have it. Install on demand rather
# than failing later with a MODULE_NOT_FOUND that looks like a code problem.
if [ ! -x "$HERE/node_modules/typescript/bin/tsc" ]; then
  echo "test dependencies missing -- installing (typescript, dayjs)..."
  # Two known environment hazards, both handled:
  #  - a half-written node_modules from an interrupted install makes npm fail with
  #    ENOTEMPTY on rename, so start from a clean directory
  #  - a root-owned ~/.npm cache makes npm fail with EPERM even for a normal user, so
  #    retry against a private cache in the system temp dir
  rm -rf "$HERE/node_modules"
  if ! (cd "$HERE" && npm install --no-audit --no-fund >/dev/null 2>&1); then
    echo "  default npm cache unusable; retrying with a temporary cache..."
    rm -rf "$HERE/node_modules"
    if ! (cd "$HERE" && npm_config_cache="$(mktemp -d)" npm install --no-audit --no-fund >/dev/null 2>&1); then
      echo "npm install failed. run it by hand:  cd \"$HERE\" && npm install"
      exit 2
    fi
  fi
  echo "dependencies installed."
  echo
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
