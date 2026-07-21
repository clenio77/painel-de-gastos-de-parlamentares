#!/usr/bin/env bash
# Automação diária do pipeline CVP-IA (cron-friendly, path-portable)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_EXEC="${PYTHON_EXEC:-python3}"
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_EXEC="$PROJECT_DIR/.venv/bin/python"
fi

LOG_FILE="$PROJECT_DIR/pipeline_exec.log"
LOCK_FILE="$PROJECT_DIR/.pipeline.lock"

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "$(date -Iseconds) Pipeline já em execução — abortando." >> "$LOG_FILE"
    exit 0
  fi
fi

{
  echo "--------------------------------------------------"
  echo "Início da execução: $(date)"
  "$PYTHON_EXEC" -m etl.run_etl_pipeline
  status=$?
  if [[ $status -eq 0 ]]; then
    echo "Execução finalizada com SUCESSO em: $(date)"
  else
    echo "FALHA detectada na execução de: $(date) (exit=$status)"
  fi
  echo "--------------------------------------------------"
  exit $status
} >> "$LOG_FILE" 2>&1
