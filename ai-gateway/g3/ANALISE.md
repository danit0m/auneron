# ANALISE: releitura dos resultados existentes (preparacao da G3)

Estado: analise offline. Nenhum modelo foi chamado. Fontes: as quatro pastas `aval-qwen-*` de `area3-evidence-20261105` (results.json e NOTAS.md) e, em `git show 1bea10d:`, o avaliador, os conjuntos v1/v2, os geradores, o protocolo e o modulo do shadow. Numeros recalculados por `work/an.py`, `work/an2.py` e `work/power_table.py` (nesta pasta).

## 1. Conferencias de integridade (fazem o resto confiavel)

- SHA-256 do `overdue_detection_shadow.py` em 1bea10d = `26ee9cb6...a372d0`, igual ao `shadow_module_sha256` gravado nas rodadas de 18/11. O prompt e o esquema de hoje sao, byte a byte, os que rodaram. Os conjuntos v1 (`80bd6f7c...6a01`) e v2 (`609c8186...10ea`) tambem conferem com os hashes das NOTAS.
- O `prompt_schema_sha256` difere entre a rodada de 2048 (`f9fa6cad...`) e as de 4096 (`75b3f2b8...`) apenas porque esse hash inclui `max_output_tokens`; o texto do prompt e o mesmo.
- Os results.json NAO trazem: tokens do prompt, `done_reason`, VRAM, nem texto. Truncamento so aparece como `error=truncated`. A frase "o modelo usa cerca de 3 GB" (NOTAS da v2) nao e rastreavel a nenhum campo medido.

## 2. As quatro rodadas

| Rodada | Config | Acertos | Invalidas | Acoes indevidas | Perdidas | p50 / p95 / max (s) | Tokens media / p95 / max |
|---|---|---|---|---|---|---|---|
| 16/11 v1, sem raciocinio (prompt v1) | temp 0, num_predict 256 | 63/80 | 0 | 17/40 | 0/40 | 3,5 / 4,4 / 6,9 | n/d |
| 18/11 v1, think, 2048 | temp 0,6 seed 0 | 67/80 | 13 (truncadas) | 0 | 0 | 11,6 / 22,7 / 82,9* | 1154 / 2048 / 2048 |
| 18/11 v1, think, 4096 | idem | 80/80 | 0 | 0 | 0 | 11,2 / 32,2 / 38,3 | 1248 / 2851 / 3347 |
| 18/11 v2 (prova), think, 4096 | idem | 79/80 | 1 (truncada) | 0 | 0 | 13,2 / 33,3 / 47,2 | 1308 / 2947 / 4096 |

*82,9 s foi a 1a chamada, a frio, apos reinicio do PC. Nas rodadas de 4096 a 1a chamada levou 9,4 s e 14,6 s (modelo ja carregado).

## 3. Por categoria

16/11 (sem raciocinio): erros so em "vence hoje" (0/8) e "futuro" (3/12). Todas as outras categorias 100%. O erro era de DATA (ignorava `days_after_due` com status aberto).

18/11 com raciocinio, 2048: todos os erros sao truncamento: futuro 5/12, 1 dia 4/12, muitos dias 3/12, poucos dias 1/8; categorias pago/ja atrasado/vence hoje/extremo: 0 truncamentos. Nenhuma decisao errada entre as 67 respostas completas.

18/11 com raciocinio, 4096: v1 sem nenhum erro em 12 categorias; v2 (15 categorias) com um unico erro, OS2-027 (+1 dia, aberto), truncado em 4096.

Tokens medios por categoria (raciocinio + JSON). Quanto mais perto da fronteira (0, +-1, +-2, +1 dia), mais o modelo pensa:

| Familia | v1 (4096) | v2 (4096) |
|---|---|---|
| pago (passado/hoje/futuro) | 620 / 1130 / 708 | 653 / 3092 (n=1) / 802 |
| ja atrasado (passado/hoje/futuro) | 606 / 687 / 996 | 430 / 2281 (n=1) / 967 |
| vence hoje | 804 | 1244 |
| futuro: -1 / -2 / distante | 1635 (todo "futuro") | 1829 / 1957 / 1261 |
| +1 dia / +2 / poucos dias | 1702 / - / 1566 | 1886 / 1054 / 1293 |
| muitos dias / extremo | 1350 / 1282 | 1060 / 1130 |

Leitura: os casos de fronteira sao os mais caros e os que truncam. Os maiores consumos da v2 foram 4096 (OS2-027, truncado), 3513, 3194, 3092, 2947.

## 4. Tokens, latencia e truncamento

- Velocidade real: latencia/tokens = ~11 ms por token, ou ~90 tokens/s (rodadas de 4096). O README do avaliador estima ~19 tok/s; a estimativa esta defasada em ~4,7x e os tempos de planejamento antigos (35-70 min por conjunto; teto 4,8 h) estao superestimados. O tempo medido foi 18,5 e 19,4 min por conjunto de 80.
- Timeout de 300 s: o maior caso real foi 47,2 s (4096 tokens = ~45 s). Ha folga de 6x; o limite de 120 s do criterio de aceite e atingivel mesmo no pior caso de 4096 tokens, mas NAO com 8192 tokens + carga a frio no pior caso (ver secao 7).
- Truncamento: 13/80 em 2048 (16,3%); 0/80 e 1/80 em 4096. Os 13 casos truncados em 2048 terminaram, em 4096, com 2062 a 3347 tokens (todos < 4096) e todos corretos. Ou seja, "invalida" em 2048 era limite de orcamento, nao erro do modelo.
- Cauda: 8/160 chamadas >= 3000 tokens (4 em cada conjunto) e 2/160 >= 3500 (ambas na v2). A taxa de truncamento em 4096 e 1/160 = 0,6% (IC95 exato 0,02% a 3,4%). Com ~170 casos no v3 espera-se de 0 a 5.
- Descoberta de reprodutibilidade: com seed 0, as 67 respostas completas de 2048 tem exatamente os mesmos tokens e o mesmo tamanho de raciocinio que as de 4096 (67/67; latencia mediana identica, razao 1,00). Mesmo build/hardware: o modelo B e deterministico por seed. Consequencias: (a) repetir seed 0 mede determinismo, nao estabilidade; estabilidade exige outras seeds; (b) aumentar `num_predict` NAO muda o prefixo gerado, entao um caso truncado e recuperavel de forma previsivel.

## 5. O que o prompt e o limite confundem

1. A melhora de 16/11 para 18/11 (17 acoes indevidas para 0) muda QUATRO coisas ao mesmo tempo: prompt v1 para v2 (que passou a mostrar `days_after_due` com regra e exemplos), raciocinio desligado para ligado, temperatura 0 para 0,6 e limite de saida 256 para 2048/4096. Nao da para atribuir ao raciocinio. O prompt v2 foi escrito justamente depois de ver o erro de 16/11.
2. Nao existe nenhuma medida do prompt v2 SEM raciocinio, e nenhuma do modelo instruct-2507. A rodada de 16/11 e de outro modelo (qwen3:4b think=false), com prompt antigo. Ela nao e evidencia sobre o braco A. Este e o maior buraco da comparacao.
3. O prompt v2 entrega `days_after_due` pronto. A tarefa que o modelo resolve e comparar um inteiro com 1 e checar um status; ele nao faz aritmetica de calendario. Bordas de ano bissexto, virada de mes e de ano exercitam o gerador e a regra, nao o modelo. O v3 as inclui para provar a regra e o gerador; o que testa o modelo nelas e se ele respeita o numero dado. Casos em que o numero contradiz as datas so existem em estratos separados (secao 4 do protocolo).
4. Truncamento entra como "invalida", nao como erro. Num limite curto isso esconde acertos; num limite longo esconde risco de cauda. As duas leituras devem ser reportadas: acertos entre respostas emitidas e acertos sobre todos os casos.
5. v1 e o conjunto de AJUSTE: o prompt (v1 para v2) e o orcamento (2048 para 4096) foram corrigidos olhando para ele; 80/80 e dentro da amostra. v2 foi a prova independente UMA vez; ao decidir "continuar em shadow" com base nela, passou a ser desenvolvimento. Um terceiro conjunto e necessario para qualquer afirmacao fora da amostra.
6. v1, v2 e v3 vem de familias de geradores parecidas (mesmas categorias de datas). Sao instancias novas, nao distribuicoes novas. Nenhum dos tres contem entrada suja, injecao, campo ausente ou contraditorio, porque o shadow monta a entrada por codigo.
7. 50/50 nao e a prevalencia de producao: o shadow so consulta o modelo depois que a regra deterministica ja marcou a conta. Em producao so aparece a classe `mark_overdue`; "acao indevida" so ocorre fora desse fluxo. A metrica de producao e a taxa de concordancia/nao-resposta nos vencidos. O v3 reporta as duas classes.
8. Amostragem: temp 0,6 com seed 0 traz UM caminho por caso. Variancia de amostragem nunca foi medida. Para o braco A (instruct-2507) a recomendacao do fornecedor costuma ser outra (nao verificada aqui, sem rede); o protocolo mantem a mesma amostragem nos dois bracos para isolar a variavel e declara o limite.

## 6. Intervalos de confianca simples (Clopper-Pearson exato, 95%)

| Observacao | Estimativa | IC95 |
|---|---|---|
| v2, think 4096: acertos | 79/80 | 93,2% a 99,97% |
| v1, think 4096: acertos (em amostra) | 80/80 | 95,5% a 100% |
| acoes indevidas 0/40 (uma classe) | 0 | limite superior 8,8% |
| acoes indevidas 0/80 | 0 | limite superior 4,5% |
| 16/11 sem raciocinio: acertos | 63/80 | 68,2% a 87,1% |
| 16/11: acoes indevidas | 17/40 | 27,0% a 59,1% |
| 2048: invalidas | 13/80 | 9,0% a 26,2% |
| 4096 (v1+v2): invalidas | 1/160 | 0,02% a 3,4% |

Limites: zero erros em 40 casos nao exclui uma taxa de ate 8,8%. Com o v3 (72 casos mark_overdue; 76 no_action nos estratos de regra) zero eventos ainda deixa o limite superior em ~5% por classe e 2,5% nos 148. Dizer "0 acoes indevidas" e um fato observado, nao uma garantia.

## 7. Maiores riscos e como reduzi-los sem tocar no prompt congelado

Riscos, em ordem:
1. Braco A sem nenhuma medicao com este prompt. Pode estar muito pior (se confundir as bordas) ou igual. Reducao: rodar A em v1+v2 primeiro (so desenvolvimento e verificacao de formato, sem ajuste), depois o v3.
2. Truncamento/cauda do braco B (~0,6% a 1,3%, concentrado nas fronteiras +-1/+-2/+1/hoje). Reducao abaixo.
3. Efeito do modelo confundido com o do raciocinio (sao pesos diferentes: instruct-2507 nao e qwen3:4b com think desligado). A comparacao decide entre dois ARTEFATOS, nao entre dois modos.
4. Variancia de amostragem nao medida (um caminho por caso). Reducao: 40 casos com seeds 1 e 2.
5. Memoria: piso de 14 GiB livres; a 1a tentativa de 16/11 foi recusada com 12,56 GiB e as rodadas de 18/11 comecaram com 14,6 a 20,4 GiB. Margem estreita no PC.
6. VRAM nunca foi medida; o "3 GB" e afirmacao sem registro.

Reduzir truncamento sem mexer em prompt, esquema ou perfil de amostragem:
- Opcao de runtime, nao de prompt: subir `num_predict` apenas para os casos truncados, num bloco separado (RB-trunc), com `num_ctx` ampliado para caber o prompt (~650-700 tokens, ESTIMATIVA: nao ha `prompt_eval_count` nos results) mais a saida: `num_predict` 6144 com `num_ctx` 8192 cabe com folga estreita; `num_predict` 8192 pede `num_ctx` 12288. Como o prefixo gerado nao muda com a seed fixa, o resultado desse bloco e comparavel e determinista. Custo: 8192 tokens a 11 ms/token = ~90 s, perto do teto de 120 s do criterio; 6144 = ~68 s.
- Isso NAO e retry dentro da rodada principal (1 tentativa por caso continua). E um bloco pos-hoc, reportado a parte, que informa "o que faltaria de orcamento" sem entrar no criterio de aceite.
- Manter o timeout de 300 s (nao ha ganho em baixa-lo) e registrar `done_reason`/tokens por chamada para medir a cauda de verdade.
- Nao usar temperatura 0 no braco B (as NOTAS registram repeticao sem fim no raciocinio).
- A decisao final deve declarar o orcamento: um 4096 que trunca ~1% e uma propriedade do braco B, e conta como "invalida" no criterio de aceite.

## 8. Fora do alcance desta analise

Nenhum dado de VRAM, de prompt_tokens, de `done_reason`, nem do modelo instruct. Sem rede e sem modelo, nada disso foi medido. A estimativa de tempo do braco A (3,5 s/caso) e um valor reaproveitado de outro modelo.
