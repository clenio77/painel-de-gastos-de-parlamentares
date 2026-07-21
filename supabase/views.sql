-- CVP-IA views, RPCs and server-side audit helpers
-- Run after schema.sql

CREATE OR REPLACE VIEW v_ranking_atual AS
SELECT DISTINCT ON (s.parlamentar_id)
  s.parlamentar_id,
  p.nome_parlamentar,
  p.partido,
  p.uf,
  p.casa,
  s.periodo,
  s.score_geral,
  s.score_atividade,
  s.score_gasto,
  s.score_transparencia,
  s.score_coerencia,
  s.dados_insuficientes,
  s.detalhes
FROM scores s
JOIN parlamentares p ON p.id = s.parlamentar_id
ORDER BY s.parlamentar_id, s.periodo DESC;

CREATE OR REPLACE VIEW v_total_gastos AS
SELECT
  COALESCE(SUM(valor), 0) AS total_gasto,
  COUNT(*) AS qtd_despesas
FROM despesas;

CREATE OR REPLACE VIEW v_top_fornecedores AS
SELECT
  fornecedor,
  cnpj_fornecedor,
  SUM(valor) AS total,
  COUNT(*) AS qtd,
  COUNT(DISTINCT parlamentar_id) AS qtd_parlamentares
FROM despesas
WHERE fornecedor IS NOT NULL
GROUP BY fornecedor, cnpj_fornecedor
ORDER BY total DESC;

CREATE OR REPLACE VIEW v_alertas_auditoria AS
SELECT
  d.id AS despesa_id,
  d.parlamentar_id,
  p.nome_parlamentar,
  d.tipo_despesa,
  d.fornecedor,
  d.cnpj_fornecedor,
  d.data_documento,
  d.valor,
  d.url_documento,
  CASE
    WHEN d.valor >= 5000 AND MOD(d.valor::numeric, 1000) = 0 THEN 'Valor Redondo'
    WHEN UPPER(COALESCE(d.tipo_despesa, '')) ~ '(ALIMENTA|REFEI)' AND d.valor > 200 THEN 'Refeição Excessiva'
    WHEN EXTRACT(DOW FROM d.data_documento) IN (0, 6)
         AND UPPER(COALESCE(d.tipo_despesa, '')) !~ '(HOSPEDAGEM|PASSAGEM|COMBUST|LOCA)' THEN 'Gasto em Fim de Semana'
    WHEN f.sancionado_tcu THEN 'Fornecedor Sancionado TCU'
    WHEN f.situacao IN ('IRREGULAR') THEN 'CNPJ Irregular'
    ELSE NULL
  END AS tipo_alerta
FROM despesas d
JOIN parlamentares p ON p.id = d.parlamentar_id
LEFT JOIN fornecedores f ON f.cnpj = d.cnpj_fornecedor
WHERE
  (d.valor >= 5000 AND MOD(d.valor::numeric, 1000) = 0)
  OR (UPPER(COALESCE(d.tipo_despesa, '')) ~ '(ALIMENTA|REFEI)' AND d.valor > 200)
  OR (
    d.data_documento IS NOT NULL
    AND EXTRACT(DOW FROM d.data_documento) IN (0, 6)
    AND UPPER(COALESCE(d.tipo_despesa, '')) !~ '(HOSPEDAGEM|PASSAGEM|COMBUST|LOCA)'
  )
  OR COALESCE(f.sancionado_tcu, FALSE)
  OR f.situacao = 'IRREGULAR';

CREATE OR REPLACE VIEW v_proposicoes_por_parlamentar AS
SELECT
  pr.*,
  p.nome_parlamentar,
  p.partido,
  p.uf,
  p.casa AS casa_parlamentar
FROM proposicoes pr
LEFT JOIN parlamentares p ON p.id = pr.parlamentar_id;

-- Latest pipeline timestamp helper
CREATE OR REPLACE FUNCTION rpc_atualizado_em()
RETURNS TIMESTAMPTZ
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT atualizado_em FROM pipeline_runs ORDER BY atualizado_em DESC LIMIT 1;
$$;

GRANT SELECT ON v_ranking_atual TO anon, authenticated;
GRANT SELECT ON v_total_gastos TO anon, authenticated;
GRANT SELECT ON v_top_fornecedores TO anon, authenticated;
GRANT SELECT ON v_alertas_auditoria TO anon, authenticated;
GRANT SELECT ON v_proposicoes_por_parlamentar TO anon, authenticated;
GRANT EXECUTE ON FUNCTION rpc_atualizado_em() TO anon, authenticated;
