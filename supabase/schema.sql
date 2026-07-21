-- CVP-IA schema (Postgres / Supabase)
-- Idempotent-ish: create if not exists

CREATE TABLE IF NOT EXISTS parlamentares (
  id BIGSERIAL PRIMARY KEY,
  id_api TEXT,
  nome_parlamentar TEXT NOT NULL,
  partido TEXT,
  uf CHAR(2),
  casa TEXT CHECK (casa IN ('camara', 'senado')),
  UNIQUE (casa, id_api)
);

CREATE TABLE IF NOT EXISTS scores (
  id BIGSERIAL PRIMARY KEY,
  parlamentar_id BIGINT NOT NULL REFERENCES parlamentares(id) ON DELETE CASCADE,
  periodo TEXT NOT NULL,
  score_geral NUMERIC(6,2),
  score_atividade NUMERIC(6,2),
  score_gasto NUMERIC(6,2),
  score_transparencia NUMERIC(6,2),
  score_coerencia NUMERIC(6,2),
  dados_insuficientes BOOLEAN DEFAULT FALSE,
  detalhes JSONB DEFAULT '{}'::jsonb,
  UNIQUE (parlamentar_id, periodo)
);

CREATE TABLE IF NOT EXISTS despesas (
  id BIGSERIAL PRIMARY KEY,
  parlamentar_id BIGINT NOT NULL REFERENCES parlamentares(id) ON DELETE CASCADE,
  periodo TEXT,
  tipo_despesa TEXT,
  fornecedor TEXT,
  cnpj_fornecedor TEXT,
  data_documento DATE,
  valor NUMERIC(14,2) NOT NULL DEFAULT 0,
  url_documento TEXT,
  chave_natural TEXT,
  UNIQUE (chave_natural)
);

CREATE TABLE IF NOT EXISTS votacoes (
  id BIGSERIAL PRIMARY KEY,
  id_api TEXT,
  casa TEXT,
  descricao TEXT,
  data TIMESTAMPTZ,
  UNIQUE (casa, id_api)
);

CREATE TABLE IF NOT EXISTS votos (
  id BIGSERIAL PRIMARY KEY,
  votacao_id BIGINT NOT NULL REFERENCES votacoes(id) ON DELETE CASCADE,
  parlamentar_id BIGINT NOT NULL REFERENCES parlamentares(id) ON DELETE CASCADE,
  voto TEXT,
  descricao_ausencia TEXT,
  divergiu_orientacao BOOLEAN DEFAULT FALSE,
  UNIQUE (votacao_id, parlamentar_id)
);

CREATE TABLE IF NOT EXISTS candidaturas_tse (
  id BIGSERIAL PRIMARY KEY,
  parlamentar_id BIGINT REFERENCES parlamentares(id) ON DELETE SET NULL,
  nome_parlamentar TEXT,
  cargo TEXT,
  ano_eleicao INT,
  total_bens NUMERIC(14,2),
  total_receitas NUMERIC(14,2),
  UNIQUE (parlamentar_id, ano_eleicao)
);

CREATE TABLE IF NOT EXISTS proposicoes (
  id BIGSERIAL PRIMARY KEY,
  parlamentar_id BIGINT REFERENCES parlamentares(id) ON DELETE SET NULL,
  casa TEXT,
  id_api TEXT,
  sigla_tipo TEXT,
  numero TEXT,
  ano INT,
  ementa TEXT,
  situacao TEXT,
  url_oficial TEXT,
  UNIQUE (casa, id_api)
);

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

CREATE INDEX IF NOT EXISTS idx_scores_periodo ON scores(periodo);
CREATE INDEX IF NOT EXISTS idx_scores_parlamentar ON scores(parlamentar_id);
CREATE INDEX IF NOT EXISTS idx_despesas_parlamentar ON despesas(parlamentar_id);
CREATE INDEX IF NOT EXISTS idx_despesas_data ON despesas(data_documento);
CREATE INDEX IF NOT EXISTS idx_despesas_cnpj ON despesas(cnpj_fornecedor);
CREATE INDEX IF NOT EXISTS idx_despesas_periodo ON despesas(periodo);
CREATE INDEX IF NOT EXISTS idx_votos_parlamentar ON votos(parlamentar_id);
CREATE INDEX IF NOT EXISTS idx_votacoes_data ON votacoes(data);
CREATE INDEX IF NOT EXISTS idx_proposicoes_parlamentar ON proposicoes(parlamentar_id);
