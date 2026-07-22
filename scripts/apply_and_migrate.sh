#!/usr/bin/env bash
# Aplica schema + RLS + views no Postgres do Supabase e roda o ETL.
#
# Credenciais necessárias (uma das opções):
#   A) DATABASE_URL=postgresql://postgres.<ref>:<SENHA>@aws-0-....pooler.supabase.com:6543/postgres
#   B) SUPABASE_DB_PASSWORD + SUPABASE_PROJECT_REF  (monta URL do pooler Session mode)
#
# Para o ETL / REST upsert:
#   SUPABASE_URL=https://<ref>.supabase.co
#   SUPABASE_SERVICE_ROLE_KEY=eyJ...
#
# Uso:
#   set -a && source .env && set +a
#   ./scripts/apply_and_migrate.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

need() {
  if [[ -z "${!1:-}" ]]; then
    echo "Falta variável: $1" >&2
    exit 1
  fi
}

resolve_db_url() {
  if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "$DATABASE_URL"
    return
  fi
  if [[ -n "${SUPABASE_DB_URL:-}" ]]; then
    echo "$SUPABASE_DB_URL"
    return
  fi
  if [[ -n "${SUPABASE_DB_PASSWORD:-}" && -n "${SUPABASE_PROJECT_REF:-}" ]]; then
    # Session pooler (IPv4-friendly). Ajuste a região se necessário via SUPABASE_POOLER_HOST.
    local host="${SUPABASE_POOLER_HOST:-aws-1-sa-east-1.pooler.supabase.com}"
    echo "postgresql://postgres.${SUPABASE_PROJECT_REF}:${SUPABASE_DB_PASSWORD}@${host}:6543/postgres"
    return
  fi
  echo "" 
}

DB_URL="$(resolve_db_url)"
if [[ -z "$DB_URL" ]]; then
  cat >&2 <<'EOF'
Não há como conectar ao Postgres.

Crie /workspace/.env com, por exemplo:

  SUPABASE_URL=https://SEU_REF.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
  SUPABASE_PROJECT_REF=seu_ref
  SUPABASE_DB_PASSWORD=sua_senha_do_banco
  # opcional se a região do pooler for outra:
  # SUPABASE_POOLER_HOST=aws-0-sa-east-1.pooler.supabase.com
  ETL_MONTHS=2

Ou uma URL completa:

  DATABASE_URL=postgresql://postgres.SEU_REF:SENHA@aws-0-....pooler.supabase.com:6543/postgres
EOF
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "Instalando cliente psql…"
  sudo apt-get update -qq
  sudo apt-get install -y -qq postgresql-client >/dev/null
fi

echo "==> Testando DNS / URL do projeto"
if [[ -n "${SUPABASE_URL:-}" ]]; then
  host="$(python3 - <<'PY'
import os
from urllib.parse import urlparse
print(urlparse(os.environ["SUPABASE_URL"]).hostname or "")
PY
)"
  if [[ -n "$host" ]]; then
    if ! getent hosts "$host" >/dev/null 2>&1; then
      echo "AVISO: host $host não resolve (NXDOMAIN). Projeto pausado/apagado ou URL errada." >&2
    else
      echo "Host OK: $host"
    fi
  fi
fi

run_sql() {
  local file="$1"
  echo "==> Aplicando $file"
  psql "$DB_URL" -v ON_ERROR_STOP=1 -f "$file"
}

run_sql "$ROOT/supabase/schema.sql"
run_sql "$ROOT/supabase/rls_policies.sql"
run_sql "$ROOT/supabase/views.sql"

echo "==> Verificando RLS (tabelas public)"
psql "$DB_URL" -c "select relname, relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and relkind='r' order by 1;"

need SUPABASE_URL
need SUPABASE_SERVICE_ROLE_KEY

PYTHON_EXEC="${PYTHON_EXEC:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_EXEC="$ROOT/.venv/bin/python"
elif [[ ! -d "$ROOT/.venv" ]]; then
  echo "==> Criando venv e instalando deps"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
  PYTHON_EXEC="$ROOT/.venv/bin/python"
fi

echo "==> Rodando ETL (ETL_MONTHS=${ETL_MONTHS:-2})"
export ETL_MONTHS="${ETL_MONTHS:-2}"
export SQLITE_PATH="${SQLITE_PATH:-$ROOT/candidatos.db}"
"$PYTHON_EXEC" -m etl.run_etl_pipeline

echo "==> Teste de escrita com anon key (deve falhar)"
ANON="${SUPABASE_ANON_KEY:-}"
if [[ -z "$ANON" && -f "$ROOT/js/panel.js" ]]; then
  ANON="$(python3 - <<'PY'
import re
t=open("js/panel.js",encoding="utf-8").read()
m=re.search(r"SUPA_KEY\s*=\s*'([^']+)'", t)
print(m.group(1) if m else "")
PY
)"
fi
if [[ -n "$ANON" ]]; then
  code=$(curl -s -o /tmp/anon_write.json -w '%{http_code}' -X POST \
    -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
    -H "Content-Type: application/json" -H "Prefer: return=minimal" \
    -d '[{"cnpj":"00000000000000","situacao":"HACK"}]' \
    "${SUPABASE_URL%/}/rest/v1/fornecedores" || true)
  echo "HTTP $code (esperado 401/403/42501)"
  head -c 300 /tmp/anon_write.json; echo
else
  echo "Anon key não encontrada — pulando teste de escrita."
fi

echo "Concluído."
