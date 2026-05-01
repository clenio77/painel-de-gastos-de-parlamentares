#!/bin/bash

# Script de automação diária para o Pipeline CVP-IA
# Este script é ideal para ser chamado pelo CRON

# 1. Definir o diretório do projeto (caminho absoluto)
PROJECT_DIR="/home/clenio/Documentos/Meusagentes/claudefree"
cd "$PROJECT_DIR"

# 2. Caminho do executável Python (ajuste se usar venv)
PYTHON_EXEC="/usr/bin/python3"

# 3. Log de execução
LOG_FILE="$PROJECT_DIR/pipeline_exec.log"

echo "--------------------------------------------------" >> "$LOG_FILE"
echo "🕒 Início da execução: $(date)" >> "$LOG_FILE"

# 4. Rodar o pipeline
"$PYTHON_EXEC" run_etl_pipeline.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "✅ Execução finalizada com SUCESSO em: $(date)" >> "$LOG_FILE"
else
    echo "❌ FALHA detectada na execução de: $(date)" >> "$LOG_FILE"
fi

echo "--------------------------------------------------" >> "$LOG_FILE"
