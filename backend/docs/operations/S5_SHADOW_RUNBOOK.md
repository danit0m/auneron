# S5 - Rodada real do shadow do agente de vencidos (homologacao sintetica)

> **Raciocinio ligado (decisao do diretor).** O `qwen3:4b` instalado e o Qwen3-4B-Thinking-2507:
> o perfil `local-qwen3-4b` agora envia `think=true`, `num_predict=4096` (raciocinio + JSON; era 2048: a avaliacao v1 de 18/11 truncou 13/80 casos no limite de 2048),
> amostragem 0,6 / 0,95 / 20 com seed 0, e o timeout do shadow sobe para 300 s. O prompt v2
> NAO mudou; o ledger distingue as chamadas pelo `prompt_template_id`
> `overdue-shadow-prompt-v2-think` (as anteriores usam `overdue-shadow-prompt-v2`).
> O texto do raciocinio NUNCA e gravado (so o tamanho em caracteres).
>
> **Orcamento NOVO obrigatorio.** O orcamento id 1 (`s5-shadow-overdue-synthetic`) foi dimensionado
> para o prompt v1 sem raciocinio (256 tokens de saida; limite 1064). Ele NAO serve (a reserva
> de pior caso agora e 7170 tokens com a margem padrao) e NAO deve ser alterado nem apagado.
> `provisionar-orcamento-shadow` cria um orcamento novo com `scope_ref`
> `s5-shadow-overdue-synthetic-think-4096` (o orcamento `-think`, dimensionado para 2048 tokens, tambem NAO e alterado); use o `ID_ORCAMENTO` novo em `-OrcamentoShadow`.

Escopo: UMA chamada real do modelo local `qwen3:4b` (Ollama, loopback) sobre UMA conta
sintetica, no banco `auneron_neon_synthetic`. O shadow so observa: o resultado do modelo
nunca decide nada. Dados reais e `APP_ENV=production` sao recusados pelo codigo.

Todos os comandos: `backend\scripts\homologacao.ps1 <comando> -Pasta <checkout>`. O cofre
(DATABASE_URL, API_KEY) ja deve estar configurado pelo diretor.

## O que garante "uma chamada"

O preco local e zero, entao o limite de dinheiro (`limit_micro = 1`, o minimo aceito) nao
restringe quantidade de chamadas. Quem garante e o limite de **tokens**:

- `limit_tokens` = reserva de pior caso de UMA tentativa (bytes de instrucoes + entrada +
  schema, + 4096 tokens de saida, + margem `INTELLIGENCE_RESERVATION_MARGIN_BPS`), calculada
  com a mesma funcao do gateway (7170 com a margem padrao de 2000 bps; era 4713 com 2048 tokens e 1064 com 256).
- Admissao atomica no UPDATE do ledger: `R + S + U + O + r <= L`. Com a chamada em voo,
  `R = L`; depois de liquidada o consumo real (centenas de tokens) fica em `S`, e uma segunda
  reserva (~L) nao cabe. Se a chamada terminar em estado desconhecido, `U = R` tambem bloqueia.
- Camadas adicionais: `OVERDUE_SHADOW_MAX_ACCOUNTS_PER_RUN=1`, `INTELLIGENCE_MAX_ATTEMPTS=1`
  (sem retry) e a `call_key` unica por pedido de aprovacao (reexecucao nao repete chamada).

## Sequencia

**Ordem obrigatoria.** A deteccao roda sozinha dentro do backend, a cada ~60 s
(`overdue_detection_maintenance_loop`). Se a conta sintetica existir com o backend no ar
SEM `-Shadow`, o backend cria o pedido de aprovacao no proximo ciclo e a rodada shadow nao
tera pedido novo para observar. Por isso a conta so e criada com o backend PARADO.
`provisionar-conta-vencida-sintetica` recusa (codigo 9) se a porta 8000 estiver ocupada.
O comando `detectar-vencidos` e o script `scheduled_detect_overdue.py` foram removidos (chamavam a rota HTTP
com a conta de sistema, que nao faz login por desenho; a deteccao roda dentro do backend a cada minuto).

1. `parar-backend -Confirmar` (confirme com `status` que a porta 8000 esta livre).
2. `backup` -> `ensaiar -ConfirmarBackup <dump>` -> `migrar -ConfirmarBackup <dump>`
   (ja existentes; so se houver migracao pendente).
3. `provisionar-orcamento-shadow -Confirmar` -> anote o `ID_ORCAMENTO`.
4. `provisionar-conta-vencida-sintetica -Confirmar` (backend PARADO) -> anote o `ID_CONTA`.
   (Pre-requisitos da deteccao ja existentes: `provisionar-deteccao-vencidos` e
   `vincular-deteccao-vencidos`; veja `consultar -Relatorio deteccao`.)
   **Antes**: `consultar -Relatorio shadow` e guarde a saida (pasta de evidencias nova).
5. Obtenha o digest aprovado do modelo (`ollama list` mostra os 12 primeiros hex; o digest
   completo de 64 hex vem de `/api/tags`). Ensaie:
   `subir-backend -Shadow -DigestModelo <64hex> -OrcamentoShadow <id> -Simular`
   (faz o pre-voo: memoria livre >= 14 GiB, Ollama e digest; nada sobe).
6. `subir-backend -Shadow -DigestModelo <64hex> -OrcamentoShadow <id> -Confirmar`.
   Codigos de recusa do pre-voo: 6 memoria insuficiente, 7 Ollama ausente/modelo ausente,
   8 digest diferente do aprovado. Variaveis injetadas so no ambiente do filho:
   `INTELLIGENCE_MODE=shadow`, `OVERDUE_SHADOW_ENABLED=true`,
   `OVERDUE_SHADOW_DATA_CLASSIFICATION=synthetic`, `OVERDUE_SHADOW_MAX_ACCOUNTS_PER_RUN=1`,
   `OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID`, `OVERDUE_SHADOW_MODEL_ID=qwen3:4b`,
   `OVERDUE_SHADOW_MODEL_DIGEST`, `INTELLIGENCE_CALL_TIMEOUT_SECONDS=300` (teto aceito pelo
   config acima de 120 somente com o shadow ligado, modo shadow e fora de production; cobre
   carga a frio + raciocinio), `INTELLIGENCE_MAX_ATTEMPTS=1`. `APP_ENV=development`.
7. **Um ciclo**: nao ha comando a rodar; a deteccao embutida dispara em ate ~60 s. Aguarde,
   com tempo maximo de 5 min, o aparecimento no `<Base>\logs\backend-*.out.log` de
   `overdue.shadow.observed` (ou `overdue.detection.completed` com `aprovacoes_solicitadas=1`;
   o shadow roda depois da deteccao). Se nada aparecer em 5 min, `parar-backend -Confirmar`,
   registre e reporte; nao repita.
8. **Depois**: `consultar -Relatorio shadow`; confira o evento `overdue.shadow.observed`.
9. `parar-backend -Confirmar`.
10. `subir-backend` (sem `-Shadow`; `INTELLIGENCE_MODE=off` volta a ser o padrao) e
    `consultar -Relatorio shadow` final.

## Criterios de aceite

- **Chamada**: exatamente 1 linha em `intelligence_model_calls`, `state=settled`,
  `model_profile=local-qwen3-4b`, `model_id=qwen3:4b`, `model_digest` == digest aprovado,
  `provider_reported_model` e `runtime_version` preenchidos, `error_class` vazio.
- **Latencia**: `latency_ms` registrada e dentro de 300 s (esperado: dezenas de segundos).
- **Raciocinio**: `prompt_template_id=overdue-shadow-prompt-v2-think`; `output_tokens` inclui
  os tokens de raciocinio; nenhum texto de raciocinio em log, ledger ou relatorio.
- **Formato**: saida valida no schema (`call_ok=true` no log; sem `error_code`).
- **Concordancia**: evento `overdue.shadow.observed` com `decision_modelo` e
  `decision_deterministica` (esperada: `mark_overdue`); registre `agreement` true/false. A
  divergencia e dado de avaliacao, nao falha.
- **Orcamento**: `settled_tokens > 0`, `reserved_tokens = 0`, `unknown_tokens = 0`.
- **Dominio inalterado pelo modelo**: a conta `SINTETICO S5 SHADOW` continua `aberto`, sem
  `account_events`, sem decisao e sem consumo de aprovacao. A rotina deterministica (sem o
  modelo) cria, por si, uma proposta consultiva e um pedido de aprovacao pendente para a conta;
  e o unico acrescimo esperado em `authenticated_advisory_proposals` e `approval_requests`
  (+1 cada, comparando antes/depois). Qualquer outro titulo, aprovacao, proposta ou evento e
  falha e interrompe a rodada.
- **Reversao**: apos o passo 10 nada do shadow permanece ligado (`INTELLIGENCE_MODE=off`).

## Risco do timeout e regra para `unknown`

`INTELLIGENCE_CALL_TIMEOUT_SECONDS=300` e o teto do config para o shadow. Estimativa de pior
caso: carga a frio ~33 s + 4096 tokens a ~19 tok/s (~215 s) + leitura do prompt ~ 250 s; 300 s
da folga de ~1,2x (apertada; so o limite inteiro de 4096 tokens chega perto, media real ~1150 tokens = ~60 s). Se mesmo assim estourar, a chamada termina em timeout. Timeout e
tratado como possivelmente enviada: a chamada fica `unknown` (`U = R`), nunca e liberada por
palpite e bloqueia nova admissao. **Regra: se a chamada ficar `unknown`, NAO repetir** (nem
reiniciar para tentar de novo, nem criar novo orcamento por conta propria). Pare o backend,
registre o relatorio `shadow` e o log, e reporte ao PO, que decide a reconciliacao.

## Fora do escopo

Nao ha segunda chamada, nem retry, nem dado real, nem producao. Repetir a rodada exige novo
orcamento (o existente fica consumido; o do prompt v1, id 1, nunca e reutilizado) e nova conta, e e decisao do PO.
