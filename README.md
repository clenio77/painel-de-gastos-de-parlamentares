# CVP-IA — Painel de Auditoria Parlamentar

Consulta, análise e validação de gastos, scores, votações, proposições e alertas de parlamentares (Câmara e Senado).

## Painel

- Abra `index.html` (estático) apontando para o projeto Supabase configurado em `js/panel.js`.
- Service Worker: `sw.js` (network-first para HTML).
- Headers/CSP: `_headers` (Netlify/CF) e `vercel.json`.

## Supabase

Aplique nesta ordem no SQL Editor:

1. [`supabase/schema.sql`](supabase/schema.sql)
2. [`supabase/rls_policies.sql`](supabase/rls_policies.sql) — **obrigatório** antes de publicar (anon só lê)
3. [`supabase/views.sql`](supabase/views.sql)

## ETL

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY
python -m etl.run_etl_pipeline
# ou cron:
./run_daily.sh
```

## Metodologia

Ver [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md).
