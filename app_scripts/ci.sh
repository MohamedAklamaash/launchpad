#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0

run() {
  local label="$1"; shift
  echo ""
  echo "▶ $label"
  if "$@"; then
    echo "✔ $label"
    ((PASS++)) || true
  else
    echo "✘ $label"
    ((FAIL++)) || true
  fi
}

echo "=== Python ==="
pip install ruff -q

for svc in gateway-service payment-service deployment-services; do
  run "syntax: $svc"  python -m compileall "$ROOT/$svc" -q
  run "ruff:   $svc"  ruff check "$ROOT/$svc"
done

echo ""
echo "=== Identity Services ==="
cd "$ROOT/identity-services"
run "pnpm install"   pnpm install --frozen-lockfile
run "build common"   pnpm --filter @launchpad/common build
run "prettier"       pnpm format
run "eslint"         pnpm lint
run "tsc"            pnpm -r --workspace-root=false exec tsc --noEmit

echo ""
echo "=== Frontend ==="
cd "$ROOT/launchpad-frontend"
run "pnpm install"   pnpm install --frozen-lockfile
run "eslint"         pnpm lint
run "tsc"            npx tsc --noEmit

echo ""
echo "================================"
echo "  Passed: $PASS  Failed: $FAIL"
echo "================================"
[[ $FAIL -eq 0 ]]
