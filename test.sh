#!/usr/bin/env bash
# test.sh — avvia gli agenti, esegue pytest, poi li spegne.
#
# Uso:
#   ./test.sh              → solo smoke test (nessuna chiamata LLM)
#   ./test.sh --integration → smoke + integration (chiamate LLM reali, ~2 min)
#   ./test.sh --no-stop    → lascia gli agenti attivi dopo i test

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}$*${NC}"; }

# ── argomenti ─────────────────────────────────────────────────────────────────
PYTEST_EXTRA_ARGS=()
STOP_AFTER=true

for arg in "$@"; do
  case "$arg" in
    --integration) PYTEST_EXTRA_ARGS+=("-m" "integration or not integration") ;;
    --no-stop)     STOP_AFTER=false ;;
    *)             PYTEST_EXTRA_ARGS+=("$arg") ;;
  esac
done

# Demo mode di default per tutti i sottoprocessi
export DEMO_MODE="${DEMO_MODE:-true}"

# ── avvio agenti ──────────────────────────────────────────────────────────────
step "[ 1/4 ] Avvio agenti (DEMO_MODE=${DEMO_MODE})..."
"$REPO_DIR/start.sh"

# ── cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
  if $STOP_AFTER; then
    step "[ 4/4 ] Spegnimento agenti..."
    "$REPO_DIR/start.sh" stop
  else
    warn "Flag --no-stop: agenti lasciati attivi."
  fi
}
trap cleanup EXIT

# ── pipeline smoke run ────────────────────────────────────────────────────────
step "[ 2/4 ] Pipeline smoke run (AAPL MSFT UCG.MI)..."
cd "$REPO_DIR"
uv run python orchestrator/main.py --tickers AAPL MSFT UCG.MI --output /dev/null
ok "Pipeline completata senza errori."

# ── pytest ────────────────────────────────────────────────────────────────────
step "[ 3/4 ] Esecuzione test..."
uv run pytest tests/ -v "${PYTEST_EXTRA_ARGS[@]}"
PYTEST_EXIT=$?

# ── esito ─────────────────────────────────────────────────────────────────────
echo ""
if [[ $PYTEST_EXIT -eq 0 ]]; then
  ok "Tutti i test sono passati."
else
  die "Alcuni test sono falliti (exit code $PYTEST_EXIT)."
fi

exit $PYTEST_EXIT
