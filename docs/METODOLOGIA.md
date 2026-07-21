# Metodologia CVP-IA (resumo)

## Nota CVP-IA

Média ponderada aproximada:

- 35% presença em votações nominais do período
- 35% economia da cota (CEAP) vs teto de referência
- 15% transparência TSE (quando disponível)
- 15% coerência partidária (voto vs orientação, quando disponível)

Períodos sem despesas **e** sem votos recebem `dados_insuficientes=true` e não devem ser interpretados como desempenho médio.

## Alertas de auditoria

Heurísticas de triagem (não prova jurídica):

1. Valor redondo alto (≥ R$ 5.000 e múltiplo de 1.000)
2. Refeição excessiva (> R$ 200 em categoria de alimentação)
3. Gasto em fim de semana (exceto hospedagem/passagem/combustível/locação)
4. Fracionamento (≥ 3 notas no mesmo dia para o mesmo CNPJ)
5. CNPJ irregular / sanção TCU (quando enriquecido)

Sempre valide o comprovante oficial (`url_documento`).

## Fontes

- API Dados Abertos da Câmara
- API Dados Abertos do Senado
- TSE (candidaturas / bens / receitas)
- BrasilAPI (situação cadastral de CNPJ)
