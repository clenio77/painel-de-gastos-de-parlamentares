-- Prefer sufficient-data periods when selecting current ranking row per parlamentar.
CREATE OR REPLACE VIEW v_ranking_atual
WITH (security_invoker = true) AS
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
  COALESCE(s.dados_insuficientes, false) AS dados_insuficientes,
  s.detalhes
FROM scores s
JOIN parlamentares p ON p.id = s.parlamentar_id
ORDER BY
  s.parlamentar_id,
  COALESCE(s.dados_insuficientes, false) ASC,
  s.periodo DESC;

GRANT SELECT ON v_ranking_atual TO anon, authenticated;
