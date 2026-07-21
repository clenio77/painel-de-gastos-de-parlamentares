#!/usr/bin/env bash
# Watchdog da coleta ETL 2025 — valida a cada 10 min se parou/travou e retoma.
#
# Uso:
#   ./scripts/watch_etl_2025.sh
#   INTERVAL_SEC=600 ./scripts/watch_etl_2025.sh
#
# Estado:
#   /tmp/etl-2025-watchdog.log  — histórico das validações
#   /tmp/etl-2025.log           — log do pipeline
#   /tmp/etl-2025-watch.state    — última métrica de progresso

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INTERVAL_SEC="${INTERVAL_SEC:-600}"
STUCK_CHECKS="${STUCK_CHECKS:-1}"   # reinicia após N ciclos sem progresso
TARGET_PARL="${TARGET_PARL:-593}"
LOG_ETL="${LOG_ETL:-/tmp/etl-2025.log}"
LOG_WD="${LOG_WD:-/tmp/etl-2025-watchdog.log}"
STATE_FILE="${STATE_FILE:-/tmp/etl-2025-watch.state}"
SESSION="${TMUX_SESSION:-etl-2025}"
DB_PATH="${SQLITE_PATH:-$ROOT/candidatos.db}"

TMUX_BIN=(tmux)
if [[ -f /exec-daemon/tmux.portal.conf ]]; then
  TMUX_BIN=(tmux -f /exec-daemon/tmux.portal.conf)
fi

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }

log() {
  local msg="[$(ts)] $*"
  echo "$msg" | tee -a "$LOG_WD"
}

ensure_session() {
  "${TMUX_BIN[@]}" has-session -t "=$SESSION" 2>/dev/null \
    || "${TMUX_BIN[@]}" new-session -d -s "$SESSION" -c "$ROOT" -- "${SHELL:-bash}" -l
}

etl_pids() {
  pgrep -f 'python3 -u etl/run_etl_pipeline.py' || true
}

pipeline_finished() {
  if [[ -f "$LOG_ETL" ]] && grep -q 'PIPELINE CVP-IA FINALIZADO' "$LOG_ETL"; then
    return 0
  fi
  return 1
}

metric_2025() {
  python3 - "$DB_PATH" <<'PY'
import sqlite3, sys, os, time
path = sys.argv[1]
if not os.path.exists(path):
    print("0 0 0 missing")
    raise SystemExit(0)
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
parl = conn.execute(
    "SELECT count(DISTINCT parlamentar_id) FROM scores WHERE periodo LIKE '2025%'"
).fetchone()[0]
scores = conn.execute(
    "SELECT count(*) FROM scores WHERE periodo LIKE '2025%'"
).fetchone()[0]
desp = conn.execute(
    "SELECT count(*) FROM despesas WHERE periodo LIKE '2025%'"
).fetchone()[0]
mtime = int(os.path.getmtime(path))
conn.close()
print(f"{parl} {scores} {desp} {mtime}")
PY
}

start_etl() {
  ensure_session
  # interrompe prompt/processo anterior na sessão
  "${TMUX_BIN[@]}" send-keys -t "$SESSION:0.0" C-c || true
  sleep 1
  local cmd='cd '"$ROOT"' && set -a && source .env && set +a && export ETL_YEARS=2025 ETL_RESUME=1 ETL_HTTP_TIMEOUT=30 && unset ETL_PERIODS ETL_FROM ETL_TO && python3 -u etl/run_etl_pipeline.py 2>&1 | tee -a '"$LOG_ETL"
  "${TMUX_BIN[@]}" send-keys -t "$SESSION:0.0" "$cmd" C-m
  log "ETL (re)iniciado na sessão tmux '$SESSION'"
}

kill_etl() {
  local pids
  pids="$(etl_pids)"
  if [[ -n "$pids" ]]; then
    log "Encerrando PID(s) travados: $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 2
    pids="$(etl_pids)"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
      sleep 1
    fi
  fi
}

load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    # format: parl scores desp db_mtime stuck_count
    read -r PREV_PARL PREV_SCORES PREV_DESP PREV_MTIME STUCK_COUNT <"$STATE_FILE" || true
  fi
  PREV_PARL="${PREV_PARL:-0}"
  PREV_SCORES="${PREV_SCORES:-0}"
  PREV_DESP="${PREV_DESP:-0}"
  PREV_MTIME="${PREV_MTIME:-0}"
  STUCK_COUNT="${STUCK_COUNT:-0}"
}

save_state() {
  echo "$1 $2 $3 $4 $5" >"$STATE_FILE"
}

validate_once() {
  local parl scores desp mtime pids alive=0 progressed=0
  read -r parl scores desp mtime <<<"$(metric_2025)"
  pids="$(etl_pids | tr '\n' ' ')"
  [[ -n "${pids// }" ]] && alive=1

  load_state

  if [[ "$parl" -gt "$PREV_PARL" || "$scores" -gt "$PREV_SCORES" || "$desp" -gt "$PREV_DESP" || "$mtime" -gt "$PREV_MTIME" ]]; then
    progressed=1
    STUCK_COUNT=0
  else
    STUCK_COUNT=$((STUCK_COUNT + 1))
  fi

  log "check: parl_2025=${parl}/${TARGET_PARL} scores=${scores} despesas=${desp} alive=${alive} pids=${pids:--} progressed=${progressed} stuck=${STUCK_COUNT}/${STUCK_CHECKS}"

  if pipeline_finished && [[ "$parl" -ge "$TARGET_PARL" ]]; then
    save_state "$parl" "$scores" "$desp" "$mtime" 0
    log "OK: pipeline finalizado com ${parl} parlamentares em 2025."
    return 0
  fi

  # Processo morto antes do fim → retomar
  if [[ "$alive" -eq 0 ]]; then
    if pipeline_finished; then
      save_state "$parl" "$scores" "$desp" "$mtime" 0
      log "Pipeline finalizado (processo já encerrado)."
      return 0
    fi
    log "ALERTA: processo parado com progresso ${parl}/${TARGET_PARL}. Retomando…"
    start_etl
    save_state "$parl" "$scores" "$desp" "$mtime" 0
    return 1
  fi

  # Vivo mas sem progresso → travado
  if [[ "$progressed" -eq 0 && "$STUCK_COUNT" -ge "$STUCK_CHECKS" ]]; then
    log "ALERTA: sem progresso em ${STUCK_CHECKS} ciclo(s) (~$((STUCK_CHECKS * INTERVAL_SEC / 60)) min). Reiniciando…"
    kill_etl
    start_etl
    save_state "$parl" "$scores" "$desp" "$mtime" 0
    return 1
  fi

  save_state "$parl" "$scores" "$desp" "$mtime" "$STUCK_COUNT"
  return 1
}

main() {
  touch "$LOG_ETL" "$LOG_WD"
  log "Watchdog iniciado (intervalo=${INTERVAL_SEC}s, stuck_checks=${STUCK_CHECKS})."
  # se não houver ETL, sobe já
  if [[ -z "$(etl_pids)" ]] && ! pipeline_finished; then
    log "Nenhum ETL ativo — iniciando coleta 2025."
    start_etl
  fi

  while true; do
    if validate_once; then
      log "Watchdog encerrando (coleta concluída)."
      exit 0
    fi
    sleep "$INTERVAL_SEC"
  done
}

main "$@"
