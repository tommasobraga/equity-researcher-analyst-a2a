#!/usr/bin/env bash
# start.sh — avvia in parallelo tutti e 6 gli agenti.
# Uso:   ./start.sh [--api]
# Stop:  ./start.sh stop

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$REPO_DIR/logs"
PID_FILE="$REPO_DIR/.agents.pid"

AGENTS=(
  "data-collector:8001"
  "news-sentiment:8002"
  "fundamental-analyst:8003"
  "risk-assessor:8004"
  "report-writer:8009"
  "portfolio-manager:8010"
)

START_API=false
[[ "${1:-}" == "--api" ]] && START_API=true

HEALTH_RETRIES=20   # tentativi ogni 1s → max 20s per processo
HEALTH_INTERVAL=1

# ── colori ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── stop ──────────────────────────────────────────────────────────────────────
stop_agents() {
  if [[ ! -f "$PID_FILE" ]]; then
    warn "Nessun file PID trovato ($PID_FILE). Niente da fermare."
    return
  fi
  echo "Fermo gli agenti..."
  while IFS= read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && ok "PID $pid terminato"
    else
      warn "PID $pid non in esecuzione"
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  ok "Tutti gli agenti fermati."
}

if [[ "${1:-}" == "stop" ]]; then
  stop_agents
  exit 0
fi

# ── pre-flight ────────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
> "$PID_FILE"

export DEMO_MODE="${DEMO_MODE:-true}"
export LLM_PROVIDER="${LLM_PROVIDER:-local}"

echo ""
echo -e "${YELLOW}Modalità:${NC} DEMO_MODE=${DEMO_MODE}  LLM_PROVIDER=${LLM_PROVIDER}"
echo ""

# ── helper: libera porta se occupata ─────────────────────────────────────────
free_port() {
  local port="$1"
  local existing_pid=""
  local _out
  _out="$(netstat.exe -ano 2>/dev/null || true)"
  if [[ -n "$_out" ]]; then
    existing_pid="$(echo "$_out" | grep ":${port} " | grep LISTENING | awk '{print $NF}' | head -1 || true)"
  fi
  if [[ -n "$existing_pid" ]]; then
    warn "Porta :${port} occupata (PID ${existing_pid}) — termino..."
    taskkill.exe //PID "$existing_pid" //F 2>/dev/null || true
    sleep 1
  fi
}

# ── avvio in parallelo ────────────────────────────────────────────────────────
declare -A AGENT_PIDS
declare -A AGENT_PORTS

for entry in "${AGENTS[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  script_name="${name//-/_}.py"
  agent_path="$REPO_DIR/agents/${name}/${script_name}"

  [[ -f "$agent_path" ]] || die "Agent non trovato: $agent_path"

  free_port "$port"

  echo "Avvio ${name} su :${port}..."
  uv run python "$agent_path" > "$LOG_DIR/${name}.log" 2>&1 &
  pid=$!
  echo "$pid" >> "$PID_FILE"
  AGENT_PIDS[$name]=$pid
  AGENT_PORTS[$name]=$port
done

# ── avvio orchestratore API ───────────────────────────────────────────────────
if $START_API; then
  api_path="$REPO_DIR/orchestrator/api.py"
  [[ -f "$api_path" ]] || die "Orchestratore non trovato: $api_path"
  free_port 8000
  echo "Avvio orchestrator/api.py su :8000..."
  uv run python "$api_path" > "$LOG_DIR/orchestrator-api.log" 2>&1 &
  api_pid=$!
  echo "$api_pid" >> "$PID_FILE"
fi

# ── attesa health-check in parallelo ─────────────────────────────────────────
echo ""
echo "Attendo che gli agenti siano pronti..."
all_ok=true

for name in "${!AGENT_PIDS[@]}"; do
  port="${AGENT_PORTS[$name]}"
  pid="${AGENT_PIDS[$name]}"
  ready=false
  for ((i=1; i<=HEALTH_RETRIES; i++)); do
    if curl -sf "http://localhost:${port}/health" > /dev/null 2>&1; then
      ready=true
      break
    fi
    sleep "$HEALTH_INTERVAL"
  done
  if $ready; then
    ok "${name} pronto (PID ${pid}, :${port})"
  else
    warn "${name} non risponde su :${port} — controlla logs/${name}.log"
    all_ok=false
  fi
done

if $START_API; then
  api_ready=false
  for ((i=1; i<=HEALTH_RETRIES; i++)); do
    if curl -sf "http://localhost:8000/health" > /dev/null 2>&1; then
      api_ready=true
      break
    fi
    sleep "$HEALTH_INTERVAL"
  done
  if $api_ready; then
    ok "orchestrator-api pronto (PID ${api_pid}, :8000)"
  else
    warn "orchestrator-api non risponde su :8000 — controlla logs/orchestrator-api.log"
    all_ok=false
  fi
fi

# ── riepilogo ─────────────────────────────────────────────────────────────────
echo ""
if $all_ok; then
  echo -e "${GREEN}════════════════════════════════════════${NC}"
  echo -e "${GREEN} Tutti i processi sono attivi.${NC}"
  echo -e "${GREEN}════════════════════════════════════════${NC}"
else
  echo -e "${YELLOW}════════════════════════════════════════${NC}"
  echo -e "${YELLOW} Avvio completato con avvertimenti.${NC}"
  echo -e "${YELLOW}════════════════════════════════════════${NC}"
fi
echo ""
echo "Log:       $LOG_DIR/"
echo "PID file:  $PID_FILE"
echo ""
if $START_API; then
  echo "Endpoints disponibili:"
  echo "  POST http://localhost:8000/research   (analyze | portfolio | full)"
  echo "  GET  http://localhost:8000/portfolio"
  echo "  GET  http://localhost:8000/health"
  echo ""
  echo "Esempio:"
  echo "  curl -X POST http://localhost:8000/research \\"
  echo "    -H 'Content-Type: application/json' \\"
  echo "    -d '{\"tickers\":[\"AAPL\",\"MSFT\"],\"mode\":\"full\"}'"
else
  echo "Orchestratore API non avviato (--no-api). Puoi usarlo via CLI:"
  echo "  uv run python orchestrator/main.py --tickers AAPL MSFT --mode full"
fi
echo ""
echo "Per fermare tutto:"
echo "  ./start.sh stop"
