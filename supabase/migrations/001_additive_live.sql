-- Additive migration aligned to the LIVE CVP-IA schema (UUID PKs).
-- Safe to re-run. Does NOT recreate existing core tables.

-- Optional columns
ALTER TABLE scores ADD COLUMN IF NOT EXISTS dados_insuficientes BOOLEAN DEFAULT FALSE;
ALTER TABLE votos ADD COLUMN IF NOT EXISTS divergiu_orientacao BOOLEAN DEFAULT FALSE;
ALTER TABLE proposicoes ADD COLUMN IF NOT EXISTS url_oficial TEXT;
ALTER TABLE proposicoes ADD COLUMN IF NOT EXISTS id_api TEXT;
ALTER TABLE proposicoes ADD COLUMN IF NOT EXISTS sigla_tipo TEXT;

-- Enrichment tables missing in production
CREATE TABLE IF NOT EXISTS fornecedores (
  cnpj TEXT PRIMARY KEY,
  razao_social TEXT,
  situacao TEXT,
  sancionado_tcu BOOLEAN DEFAULT FALSE,
  detalhe TEXT,
  verificado_em TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id BIGSERIAL PRIMARY KEY,
  atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT,
  detalhes JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_despesas_ano_mes ON despesas(ano, mes);
CREATE INDEX IF NOT EXISTS idx_scores_periodo ON scores(periodo);
CREATE INDEX IF NOT EXISTS idx_despesas_cnpj ON despesas(cnpj_fornecedor);

ALTER TABLE fornecedores ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fornecedores_select_public ON fornecedores;
DROP POLICY IF EXISTS pipeline_runs_select_public ON pipeline_runs;
CREATE POLICY fornecedores_select_public ON fornecedores FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY pipeline_runs_select_public ON pipeline_runs FOR SELECT TO anon, authenticated USING (true);
