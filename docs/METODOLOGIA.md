# Metodologia CVP-IA (resumo)

## Nota CVP-IA

Média ponderada aproximada:

- 35% presença em votações nominais do período
- 35% economia da cota (CEAP) vs teto de referência
- 15% transparência TSE (quando disponível)
- 15% coerência partidária (voto vs orientação, quando disponível)

Períodos sem despesas **e** sem votos recebem `dados_insuficientes=true` e não devem ser interpretados como desempenho médio.

## Comparativo vs pares

Na ficha do parlamentar e no ranking, a nota é comparada à **mediana** dos pares da mesma casa:

- mesmo **partido**
- mesma **UF**

Parlamentares com `dados_insuficientes` são excluídos do cálculo. Se o grupo tiver menos de 3 pares, a UI exibe “amostra insuficiente”.

## Ficha do fornecedor (CNPJ)

Despesas são agregadas pelos dígitos do CNPJ/CPF (não pelo nome fantasia). A ficha mostra:

- totais (valor, notas, parlamentares)
- breakdown por tipo de despesa
- lista de parlamentares (com link para a ficha)
- maiores notas com comprovante
- situação cadastral / TCU quando enriquecida pelo ETL

Shift+clique no card de fornecedor filtra a aba Gastos.

## Alertas de auditoria

Heurísticas de triagem (não prova jurídica):

1. Valor redondo alto (≥ R$ 5.000 e múltiplo de 1.000)
2. Refeição excessiva (> R$ 200 em categoria de alimentação)
3. Gasto em fim de semana (exceto hospedagem/passagem/combustível/locação)
4. Fracionamento (≥ 3 notas no mesmo dia para o mesmo CNPJ)
5. CNPJ irregular / sanção TCU (quando enriquecido)

Cada alerta na UI traz **evidência** (regra, gravidade, valor, data, CNPJ, comprovante). O botão **Exportar CSV** baixa todos os alertas do filtro atual (gravidade/tipo), não só os 200 exibidos na lista.

Sempre valide o comprovante oficial (`url_documento`).

## Fontes

- API Dados Abertos da Câmara
- API Dados Abertos do Senado
- TSE (candidaturas / bens / receitas)
- BrasilAPI (situação cadastral de CNPJ)
