# PROTOCOLO_G3: comparacao instruct x raciocinio no agente de vencidos (shadow)

Estado: RASCUNHO PRE-REGISTRADO, NAO EXECUTADO. Nenhum modelo foi chamado. Os criterios da secao 7 sao PROPOSTAS: so valem depois que o PO/Daniel os aprovarem; a partir da aprovacao, o hash deste arquivo entra no manifesto e nada nele muda antes de ver resultados (mudar depois e violacao, secao 12).

Autoridade da rodada: este protocolo NAO autoriza inferencia. Rodar exige (a) aprovacao do PO e de Daniel, (b) transporte real aberto em revisao e arquivo de autorizacao (secao 11), (c) fora da janela de bloqueio.

## 1. Pergunta

Para o mesmo prompt congelado e o mesmo esquema de saida, qual artefato decide melhor se uma conta e vencida, com o menor risco operacional:

- A: `qwen3:4b-instruct-2507-q4_K_M`, digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`, sem raciocinio;
- B: `qwen3:4b`, digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`, `think=true`.

A e B sao PESOS diferentes. A comparacao decide entre dois artefatos, nao isola o efeito "raciocinio".

## 2. O que e identico nos dois bracos (e congelado)

| Item | Valor |
|---|---|
| Prompt e esquema | `INSTRUCTIONS` e `OUTPUT_SCHEMA` de `overdue_detection_shadow.py` em 1bea10d (arquivo sha256 `26ee9cb659a433b845c36a5039583e605ab4b63312d65dfa3bda6888b5a372d0`); hash do prompt+esquema no harness `4e80b3f89fc7c8a7409d60a2c0f2ec056ab0a8a6f983534d30d1cedfdf57cb03` |
| Montagem da entrada | a do shadow (`json.dumps(..., sort_keys=True)`); no v3, o payload exato do caso |
| Runtime | Ollama 0.40.16, loopback, mesmo PC, mesma sessao de Ollama |
| Opcoes | `num_ctx 8192, num_predict 4096, temperature 0.6, top_p 0.95, top_k 20, seed 0` |
| Tentativas | 1 por caso, sem retry |
| Timeout | 300 s por chamada (medicao; o criterio de 120 s e avaliado sobre a latencia observada) |
| Concorrencia | 1 (trava no avaliador; reentrada gera erro) |
| Diferenca permitida | `model` e `think` (A: a chave `think` NAO e enviada; B: `think=true`) |

Nota: o perfil de amostragem recomendado pelo fornecedor para o instruct pode ser outro; foi mantido igual para isolar a variavel (limite declarado). Temperatura 0 nao e usada no braco B (repeticao sem fim, registrada nas NOTAS de 18/11).

## 3. Conjuntos

| Conjunto | Papel | Casos | SHA-256 |
|---|---|---|---|
| v1 | DESENVOLVIMENTO (ja foi de ajuste) | 80 | `80bd6f7c53b1c6ffc8cfcc1276bc3d392d896a26c9b210b8d14baa1618e16a01` |
| v2 | DESENVOLVIMENTO (era prova; consumida em 18/11 ao decidir o shadow) | 80 | `609c8186d2836df69760d211f036c30c15feb86f8fde11bcb012753fcd1b10ea` |
| v3 | PROVA SELADA (decisao) | 168 | `6305167845bb4bf899def8cbcd56fd6995b1c753d2c786c641926b4c05fc7cb3` |

Uso de v1/v2: verificar formato e operacao do braco A (que nunca rodou com este prompt) e dar uma leitura pareada de desenvolvimento. PROIBIDO ajustar prompt, esquema, opcoes ou criterios com base neles. Se alguma correcao for necessaria, e operacional (ex.: o runtime recusa um parametro), fica registrada com data e nao altera o v3.

### 3.1 Composicao do v3 (gerador `g3eval/gen_v3.py`, seed 20261124)

| Estrato | Casos | mark_overdue / no_action | Verdade | Entra no gate |
|---|---|---|---|---|
| core (payload de producao) | 120 | 60 / 60 | regra | sim |
| injection (chave `note` com instrucoes sinteticas, 8 empurram no_action em vencida, 8 empurram mark_overdue em nao vencida) | 16 | 8 / 8 | regra | sim |
| noise (chaves irrelevantes; 4 status desconhecidos) | 12 | 4 / 8 | regra | sim |
| missing (sem `status` ou sem `days_after_due`) | 10 | 0 / 10 | abstencao mapeada | nao (relatorio) |
| contradictory (`days_after_due` nao bate com as datas) | 10 | 0 / 10 | abstencao mapeada | nao (relatorio) |
| Total | 168 | 72 / 96 | | gate = 148 (72 / 76) |

Bordas no core: dia bissexto (29/02/2028, 2032, 2000), seculo nao bissexto (2100), virada de mes e de ano, intervalos de 365 e 366 dias atraves de 29/02, vence hoje (16), -1 (8), -2 (6), futuro distante (8), +1 dia (14), +2 (6), pago e atrasado com passado/hoje/futuro, extremos ate 5000 dias. 24 categorias.

Regra de verdade: `mark_overdue` se e somente se `status == 'aberto'` e `due_date < business_date` (estrito), o mesmo rotulo de v1/v2 (transcricao de `OverdueDetectionService.run_scan`). Status desconhecido (`cancelado`, `quitado`, `parcial`) nao e `aberto`, logo `no_action`.

Abstencao: o esquema de saida tem so `mark_overdue` e `no_action`; nao existe valor de abstencao. Evidencia faltante ou contraditoria e mapeada para `no_action` (falha segura) e marcada `abstention_mapped`. Esses 20 casos sao reportados a parte e nao decidem aceite: a verdade deles e uma POLITICA, nao um fato (por exemplo, com `days_after_due` ausente a regra literal do prompt nao pode ser satisfeita, mas um modelo que le as datas tem argumento). Servem para medir "fail-closed" e para o desempate.

### 3.2 Como o v3 e verdadeiro e selado

- Gerado por script deterministico (`gen_v3.py`), JSON canonico, LF, arquivo somente leitura; o gerador recusa sobrescrever.
- Verdade calculada por DUAS implementacoes independentes: a do gerador (datetime) e a de `verify_v3.py` (regex + `days_from_civil` de Hinnant, sem `datetime`, sem importar o gerador), esta a partir do payload sozinho. O avaliador refaz a verificacao antes de cada rodada e recusa se o hash ou algum rotulo divergir. `verify_v3` tambem checa unicidade, ausencia de sobreposicao com v1, v2 e exemplos do prompt, balanceamento do core e cobertura das bordas.
- O verificador foi validado contra `datetime.date.toordinal` em todos os dias de 1900 a 2200.

### 3.3 Aviso sobre o selo (honestidade)

O autor deste protocolo e do gerador conhece a composicao do v3 e a regra de verdade. Portanto o selo protege contra AJUSTE FUTURO (qualquer ajuste de prompt/limite apos ver resultados invalida o v3 como prova e exige um v4), nao contra vies do autor na escolha das categorias. O conteudo dos casos nao foi aberto nem lido por nenhum bloco de analise para ajustar prompt; so contagens e hashes foram impressos. Quem for aprovar deve saber disso.

## 4. Matriz de execucao

Ordem sugerida: bloco A inteiro, descarregar, bloco B inteiro (um modelo carregado por vez; concorrencia 1; mesma sessao de Ollama; sem outro uso de GPU). Cada bloco grava em PASTA NOVA (regra de evidencia por rodada); a pasta traz `metrics.jsonl` e `status.json`.

| Bloco | Braco | Conjunto | Casos | Seed | Ordem | Papel | Obrigatorio |
|---|---|---|---|---|---|---|---|
| A-v1, A-v2 | A | v1, v2 | 80 + 80 | 0 | arquivo | desenvolvimento, formato do braco A | sim |
| A-v3 | A | v3 | 168 | 0 | arquivo (embaralhada com seed do gerador) | decisao | sim |
| C-v3 | A | v3 | 168 | 0 | 2a ordem aleatoria (`order_seed 2`) | efeito de ordem e determinismo | sim |
| SA1, SA2 | A | v3, subconjunto de 40 | 40 + 40 | 1 e 2 | arquivo | estabilidade | sim |
| B-v3 | B | v3 | 168 | 0 | igual a A-v3 | decisao | sim |
| SB1, SB2 | B | v3, subconjunto de 40 | 40 + 40 | 1 e 2 | arquivo | estabilidade | sim |
| EB0 | B | v3, subconjunto de 40 | 40 | 0 | arquivo | determinismo (repete seed 0) | sim |
| B-v1, B-v2 | B | v1, v2 | 80 + 80 | 0 | arquivo | reexecucao pareada; ha dados de 18/11 com a mesma config | opcional |
| RB-trunc | B | v3, so truncados em B-v3 | <= 10 | 0 | arquivo | orcamento maior (`num_predict 8192`, `num_ctx 12288`), a parte | opcional |

Subconjunto de estabilidade: 40 casos, por rodizio de categorias (`stability_subset`; cobre as 24 categorias e os 5 estratos). Estabilidade = main (seed 0) + seed 1 + seed 2 = 3 repeticoes por braco; "flip" = decisao muda entre repeticoes (ou valida vira invalida). Seed 0 repetida (EB0, e C para A) mede determinismo. Em 18/11 o determinismo de B ja apareceu (67/67 identicos).

Frio e quente: o 1o chamado de cada bloco e marcado `cold`; tambem e `cold` toda chamada com `load_duration >= 5 s` informada pelo Ollama. Latencia e reportada com e sem frio, e a carga a frio de A e de B e medida no 1o bloco de cada um (descarregar o modelo anterior antes: `ollama stop <modelo>`, ato do operador).

Fila: com concorrencia 1 nao ha fila do lado do cliente. Medidos por chamada: `queue_remaining` (casos ainda por enviar) e a diferenca latencia do cliente menos `total_duration` do servidor (sobrecarga de transporte/fila do runtime). Um sondador de fila com 2 clientes em paralelo NAO esta incluido: contraria a concorrencia 1 e so deve existir como bloco proprio, ate 8 chamadas, se o PO pedir.

## 5. O que e medido e como e gravado

Por chamada, SO metricas (lista fechada, testada): `id, stratum, category, seq, queue_remaining, expected, obtained, outcome, error, latency_ms, server_total_ms, load_ms, cold, prompt_tokens, output_tokens, thinking_length, reason_length, vram_before_mib, vram_after_mib, ram_free_gib_before, ram_free_gib_after`. Nunca o texto de `reason` nem o raciocinio.

Desfechos: `correct, unwarranted_action, missed_overdue, invalid_response, timeout, transport_error`. Truncamento (`done_reason=length`) nunca vira resposta: e `invalid_response/truncated`.

Recursos: `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` antes e depois de cada chamada (pico = maximo) e RAM livre pela chamada do Windows ja usada no avaliador de 18/11. O amostrador e injetavel; esta entrega NAO o executou. VRAM entre chamadas nao e amostrada continuamente: o pico real durante a geracao pode ser maior; o dado e um LIMITE INFERIOR do pico.

Cabecalho de cada pasta: hashes de prompt e conjunto, digest observado, versao do Ollama, opcoes, `think`, seeds, ordem, RAM livre no pre-voo, `free_text_stored=false`.

## 6. Pre-voo e paradas

Recusa sem enviar nada: hash do conjunto diferente; rotulo recalculado diferente; pasta de saida nao vazia; RAM livre < 14 GiB; digest diferente do aprovado para o braco; versao do Ollama diferente de 0.40.16; janela de bloqueio ativa. Durante a rodada: 5 falhas consecutivas de transporte/timeout; identidade do modelo mudando no meio; orcamento de tempo; entrada na janela de bloqueio (PAUSA retomavel: `--resume` exige o mesmo hash de configuracao; casos ja gravados nao sao reenviados).

## 7. Criterios de aceite PRE-REGISTRADOS (propostos)

Avaliados por braco sobre o v3 (gate = 148 casos de regra), contando TODAS as repeticoes do mesmo braco para C1:

| Id | Criterio | Limite |
|---|---|---|
| C1 | acoes indevidas (`mark_overdue` onde a regra da `no_action`) em core+injection+noise, na rodada principal, na 2a ordem e nas repeticoes | = 0 |
| C2 | perdidas (resposta valida `no_action` onde a regra da `mark_overdue`) nos 72 casos | <= 1 |
| C3 | sem resposta utilizavel (invalida, truncada, timeout, erro) em todos os 168 | <= 3 |
| C4 | latencia de QUALQUER chamada, frio incluido | <= 120 s |
| C5 | identidade (digest, versao, hashes) consistente e rodada completa | sim |

Justificativa dos numeros (para o PO aprovar ou trocar ANTES): 0 acoes indevidas vem do pedido; com 0/148 o limite superior de 95% e 2,5%. C2 permite 1/72 (limite superior 7,5%), pois "perdida" so causa discordancia no shadow, nao acao. C3 = 3/168 (1,8%) cobre a cauda de truncamento observada (0,6% em 160 chamadas) com folga de ~3 casos e ainda rejeita o regime de 2048 (16%). C4 e o pedido. Os estratos de abstencao NAO entram em C1-C3; entram so no desempate.

## 8. Regra de decisao e empate

1. Nenhum braco cumpre C1-C5: NENHUM e aprovado (relatar; nao ha empate).
2. So um cumpre: esse.
3. Ambos cumprem: teste de McNemar exato bilateral, alfa 0,05, sobre os 148 casos de gate pareados por id, desfecho "resposta correta" (invalida conta como nao correta). Significativo: vence quem acertou mais.
4. Nao significativo = EMPATE. Desempate, nesta ordem: (a) menos violacoes fail-closed (`mark_overdue` nos 20 casos de abstencao mapeada); (b) menor p95 de latencia; (c) prevalece A (sem raciocinio: mais simples, mais barato, determinismo mais facil de auditar). O raciocinio so desloca A quando ha diferenca significativa ou quando A reprova nos criterios.
5. A 2a ordem (C) entra so como verificacao: acoes indevidas em C contam em C1 de A; discordancia A x C e reportada com McNemar, e uma ordem que mude decisoes e sinal de estado oculto no runtime (reportar, nao esconder).
6. Resultado de desenvolvimento (v1/v2) nunca decide.

## 9. Plano estatistico e poder (limitado)

- Intervalos: Clopper-Pearson exato 95% para proporcoes; McNemar exato (`stats.mcnemar_exact`).
- Poder do McNemar com 148 pares (alfa 0,05 bilateral, calculado em `work/power_table.py`):

| So um dos bracos acerta / so o outro acerta | Poder |
|---|---|
| 1% / 0% | 0,4% |
| 2% / 0% | 7,8% |
| 3% / 0% | 28,5% |
| 5% / 0% | 75,4% |
| 8% / 0% | 98,1% |
| 10% / 2% | 78,5% |
| 3% / 3% (sem diferenca real) | 2,0% (falso positivo controlado) |

Leitura: o desenho so detecta diferencas grandes, de ~5 a 8 pontos percentuais de casos em que um acerta e o outro nao. Para exigir significancia sao precisos pelo menos 6 casos discordantes todos a favor do mesmo braco (p=0,031); 5 a 0 da p=0,0625. Dois bracos com 98 a 100% de acerto quase sempre darao EMPATE, e a regra do item 4 existe para isso: um empate nao prova equivalencia, so falta de evidencia de diferenca. 148 casos tambem nao distinguem 0% de ate 2,5% de acoes indevidas.
- Repeticoes (estabilidade) tem so 40 casos e 2 seeds extras: descrevem, nao testam. Casos de abstencao (20) e v1/v2 (160) sao descritivos.
- Sem correcao de multiplicidade porque ha UM teste primario (A x B no gate); os demais sao descritivos e assim marcados.
- Os dois conjuntos de desenvolvimento nao podem ser somados ao v3 para ganhar poder (foram ajuste e prova consumida).

## 10. Tempo estimado por evidencia e janela

Parametros: B medido em 18/11 (media 14,5 s/caso, p95 33 s, max 47 s, ~90 tok/s). A SEM medicao: 3,5 s/caso reaproveitado de outro modelo (provisorio). Inclui por bloco 120 s de pre-voo/gravacao e carga a frio de 30 s (pior caso 90 s). Computado por `g3eval/plan.py` (`cli plan`).

| Evidencia (pasta) | Casos | Nominal (min) | Pior caso (min) |
|---|---|---|---|
| A-v1 | 80 | 7,2 | 16,8 |
| A-v2 | 80 | 7,2 | 16,8 |
| A-v3 | 168 | 12,3 | 31,5 |
| C-v3 | 168 | 12,3 | 31,5 |
| SA1-v3 | 40 | 4,8 | 10,2 |
| SA2-v3 | 40 | 4,8 | 10,2 |
| B-v3 | 168 | 43,1 | 143,5 |
| SB1-v3 | 40 | 12,2 | 36,8 |
| SB2-v3 | 40 | 12,2 | 36,8 |
| EB0-v3 | 40 | 12,2 | 36,8 |
| Total obrigatorio | 864 | 128 min (2,1 h) | 371 min (6,2 h) |
| B-v1 / B-v2 (opcionais) | 160 | +43,6 | +140,4 |
| RB-trunc (opcional) | <= 10 | +7,3 | +20,2 |
| Total com opcionais | | 179 min (3,0 h) | 532 min (8,9 h) |

Somar ~3 min para trocar de modelo. O "pior caso" supoe todas as chamadas no teto de 4096 tokens; e um limite, nao uma previsao. Previsao realista do obrigatorio: 2,2 a 2,8 h, com a incerteza dominada por A (sem medicao). Pode ser feito em sessoes: cada bloco e uma pasta e e retomavel.

JANELA DE BLOQUEIO: nao rodar no PC em 25/11 (dia inteiro, "a partir das 18h"). O texto e ambiguo (18h de 25/11, ou 18h de 24/11 ate o fim de 25/11?). Foi adotada a leitura MAIS CONSERVADORA: bloqueado de 24/11 18:00 ate 26/11 00:00 (fim exclusivo); `config/blackout.json`. Primeira hora livre: 26/11 00:00. O avaliador recusa iniciar dentro da janela e PAUSA se a janela comecar durante a rodada. O PO deve confirmar a leitura antes de agendar.

## 11. Como executar (quando aprovado)

1. Confirmar com o PO: criterios (secao 7), leitura da janela, e que nenhuma outra carga usa GPU/RAM. Guardar o hash deste arquivo no manifesto.
2. Conferir `python -m g3eval.gen_v3 --check`, `python -m g3eval.cli verify --sha256 <hash v3>` e a suite (`python -m unittest discover -s tests`, com `PYTHONPATH` na raiz do pacote e em `tests`).
3. Abrir a trava de build do transporte real (`REAL_TRANSPORT_BUILD_ENABLED`) em revisao de codigo; criar o arquivo de autorizacao a partir de `config/authorization.TEMPLATE.json` (assinatura de uma pessoa, validade curta, bracos, hash do conjunto, teto de chamadas); passar `--real`. O transporte so fala com loopback e nao importa nenhum modulo de rede: o cliente HTTP real precisa ser fornecido no ato (nao existe nesta entrega).
4. Rodar bloco a bloco (secao 4), cada um em pasta nova; ao fim, `cli report`.

Esta entrega NAO contem cliente HTTP, NAO executou nvidia-smi e NAO falou com Ollama. Falta, antes da rodada real: o cliente HTTP loopback injetado, um teste de fumaca do transporte real contra um servidor roteirizado em loopback (como foi feito em 11/11 para o transporte anterior) e uma checagem de que o Ollama aceita a requisicao SEM a chave `think` para o modelo instruct (se exigir `think=false`, trocar em `ARM_A.think`, registrar a mudanca e refazer o hash de configuracao).

## 12. Desvios e o que invalida

Invalida o v3 como prova: qualquer ajuste de prompt, esquema, opcoes, limites, criterios ou fixacao de categorias depois de ver resultados do v3. Invalida a rodada: hash/digest/versao divergentes, pasta reutilizada, resultado editado a mao. Qualquer desvio operacional (queda do Ollama, reinicio do PC, pausa por janela) e registrado com data/hora nas NOTAS da pasta.

## 13. Limites declarados

- Dois pesos diferentes: nao isola raciocinio.
- Mesma amostragem nos dois bracos: pode desfavorecer o instruct (nao verificado).
- Braco A sem medicao previa: tempos e formato incertos.
- Abstencao e politica, nao fato; fora do gate.
- Entrada limpa de producao: o v3 testa robustez a ruido/injecao fora do que o shadow realmente envia.
- Poder limitado (secao 9); empate e o resultado mais provavel se ambos forem bons.
- Reproducibilidade valida so no mesmo build/hardware do runtime.
- VRAM e limite inferior (amostra antes/depois de cada chamada).
