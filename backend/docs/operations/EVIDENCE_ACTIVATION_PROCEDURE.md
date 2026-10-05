# Procedimento de ativação da coleta de evidência (`observed_fact`)

**Escopo:** VALUE-3.4D-1 — Activation Safety Foundation.
**Natureza:** procedimento operacional. **Este documento NÃO autoriza ativação.**
A ativação de qualquer janela exige autorização explícita e formal do PO,
registrada fora deste arquivo.

> Este arquivo não contém T0 real, dado de cliente, credencial, nem assume que
> exista um ambiente real. Todo valor entre `<...>` é um **placeholder** a ser
> preenchido no momento da execução e **nunca** commitado neste repositório
> (o repositório é público).

## 1. Fronteira

* Coletar `observed_fact` significa registrar que **um pagamento foi
  REGISTRADO no Auneron em momento posterior ao escalonamento**
  (`observed_at` = início da transação no relógio do PostgreSQL).
* **Correlação temporal ≠ causalidade.** Nenhum passo deste procedimento
  autoriza afirmar que o escalonamento causou o pagamento, nem alimentar
  NBA, política ou autonomia.
* A coleta **não** habilita decisão, aprovação, recuperação, mutação
  financeira ou autonomia: o worker só faz `INSERT` em
  `escalation_observations` (provado por teste de runtime e de AST).

## 2. Estado de prontidão (o que existe / o que ainda bloqueia)

| Pré-requisito | Estado |
|---|---|
| Default DEV `MAINTENANCE_ENABLED=false` versionado no compose | **Disponível (D-1)** |
| Contrato estrito do floor + preflight (`scripts/evidence_floor_preflight.py`) | **Disponível (D-1)** |
| Log `application_started` com estado dos dois gates | **Disponível (D-1)** |
| Build de ativação em worktree limpo + verificação de proveniência (SHA, conteúdo, `extras == 0`, image ID) | **Depende de D-2 — ATIVAÇÃO BLOQUEADA até D-2 aplicado e verificado** |
| Identidade de build verificável (`AUNERON_GIT_SHA/DIRTY`, `source_digest` `sd1`) e verificação no CI | **Disponível (D-2a)** — apenas identidade; **não** é proveniência persistente |
| Venue com evidência operacional real | **NÃO VERIFICADO.** DEV serve **somente** a ensaio mecânico; não se fabricam pagamentos nem escalonamentos para satisfazer critérios |
| Autorização do PO para a janela | Obrigatória, caso a caso |

## 2.1 Identidade de build (D-2a) — o que prova e o que NÃO prova

Cadeia: o processo de build fornece **alegações** (`GIT_SHA`, `GIT_DIRTY`) →
`ARG` → `LABEL` + `ENV AUNERON_GIT_SHA` / `AUNERON_GIT_DIRTY` → o processo
valida o formato e **mede** o `source_digest` (algoritmo `sd1`: `app/**/*.py`,
`migrations/**/*.py` e `requirements.txt`; caminhos ASCII, `\r\n` → `\n`,
symlink rejeitado). A cadeia é **conjuntiva**: SHA com 40 hex minúsculos,
`dirty` exatamente `false` e digest calculável. O digest nunca resgata um SHA
`unknown`/inválido nem `dirty=true`.

- Sem alegação (build manual com `docker compose build` e nenhuma variável):
  `GIT_SHA=unknown` e `GIT_DIRTY=unknown` → a imagem constrói e executa, mas a
  identidade é **inválida** (`identity_sha_invalid`) — jamais uma identidade
  limpa fabricada. O default de `dirty` **nunca** é `false`.
- Alegações de um checkout real: `python scripts/verify_build_identity.py
  claims` (`dirty` considera o repositório inteiro, `--untracked-files=all`).
  Esta worktree de trabalho tem arquivos não rastreados → `dirty=true`; o build
  de ativação exige um worktree limpo do `<SHA>`.
- Verificação independente (CI e, depois, preflight): `python
  scripts/verify_build_identity.py verify --image <tag> --git-rev <SHA>
  --require-clean` recompõe o `sd1` a partir dos **objetos do commit** e o
  compara com o digest medido **dentro** da imagem (C1–C5). O valor reportado
  pela própria imagem nunca é usado como esperado.
- O `application_started` registra o diagnóstico (`build_identity_state`,
  `build_identity_code`, `build_git_sha`, `build_git_dirty`,
  `build_source_digest`, `build_source_digest_algorithm`). **Somente
  diagnóstico:** identidade inválida **não** bloqueia o startup nem altera
  nenhum worker.

**Limites declarados (não entregues em D-2a):**

1. O runtime só valida o **formato** da alegação; uma alegação falsa só é
   detectada pela verificação externa (digest do commit × digest da imagem).
2. `extras != 0` da imagem inteira (arquivos fora do escopo do `sd1`) e a
   verificação do container (image ID == tag verificada; ENV do container ==
   ENV da imagem) continuam itens do procedimento de ativação.
3. O `sd1` não cobre dependências transitivas, imagem base nem arquivos fora
   do escopo.
4. Nenhuma proveniência persistente (D-2b) nem fail-closed do produtor
   automático: **ATIVAÇÃO BLOQUEADA até D-2 completo (D-2a + D-2b)**.

## 2.2 Proveniência persistente (D-2b) — mecanismo, não ativação

Todo **novo** `observed_fact` produzido pelo worker automático referencia um
`EvidenceProvenanceContext` (`provenance_context_id`: *sob qual contexto
verificável este Auneron interpretou a evidência*) e carrega
`producer_pass_id` (*em qual passagem do worker foi materializado*). O contexto
é imutável e endereçado por conteúdo (`epc1`) e reúne: SHA e `dirty=false` do
build, `source_digest` (`sd1`), `producer_spec`
(`escalation_payment_observation:v1`), fingerprint do produtor (`pf1`), floor
efetivo canônico (UTC) e as revisões de schema **esperada** (código) e **real**
(banco).

**Fail-closed do produtor.** Identidade de build inválida/`dirty`, fingerprint
indisponível ou divergente do pin, floor sem fuso, revisão esperada/real
ausente ou divergente, ou falha ao gravar/validar o contexto ⇒ o worker
**abstém**: nenhuma `observed_fact` é escrita, a aplicação/API continuam no ar
e um código diagnóstico estável é emitido (`escalation_payment_observation.
provenance_blocked`). Isso é independente dos gates (`MAINTENANCE_ENABLED` e
floor) e **não** ativa nada.

**Legado.** `observed_fact` anterior ao D-2b permanece com proveniência `NULL`
(sem backfill, sem sentinela); `human_assessment` nunca recebe proveniência
automática. O `CHECK` correspondente é criado `NOT VALID`: vale para toda linha
nova, tolera o legado. **Não** execute `VALIDATE CONSTRAINT` fora da preparação
de ativação, depois de conhecer o legado real.

**Auditoria (somente leitura):**

```bash
python scripts/evidence_provenance_report.py report --limit 20
```

Mostra revisão esperada × real, fingerprint medido × pin, contextos com
verificação de integridade (`epc1` armazenado == recomputado), contagem de
observations/passes por contexto e a contagem de `observed_fact` legado. Saídas:
0 = íntegro, 2 = integridade violada, 3 = banco indisponível. O script não
corrige, não faz backfill, não valida constraint, não altera floor, não ativa
worker e não migra banco.

**Pré-condição de ativação (somente com autorização do PO):** a contagem de
`observed_fact` legado é conhecida e decidida, a migration está aplicada no
alvo, e o relatório de auditoria está íntegro. Esta seção descreve mecanismo:
**ATIVAÇÃO BLOQUEADA** até o PO autorizar a janela.

## 3. Os dois gates (independentes)

| `MAINTENANCE_ENABLED` | floor | efeito |
|---|---|---|
| `false` | ausente | nenhum worker global, nenhum evidence worker |
| `false` | válido | nenhum worker global, **somente** o evidence worker |
| `true` | ausente | workers globais normais, nenhum evidence worker |
| `true` | válido | workers globais normais + evidence worker (uma vez) |

Produção usa `docker-compose.prod.yml` (arquivo independente, não overlay) e
**proíbe** `MAINTENANCE_ENABLED=false` (`validate_environment`). Este
procedimento não altera nem se aplica ao compose de produção.

## 4. DEV: manter `MAINTENANCE_ENABLED=false` de forma durável

O compose de DEV (`backend/docker-compose.yml`) passa
`MAINTENANCE_ENABLED: ${MAINTENANCE_ENABLED:-false}` ao backend:

* **sem variável → `false`** (fail-closed, versionado);
* ligar manutenção é **explícito**: `MAINTENANCE_ENABLED=true` no ambiente do
  comando (bash) ou `$env:MAINTENANCE_ENABLED = "true"` (PowerShell).

Não fazem parte do procedimento normal: override em `%TEMP%` e o arquivo
histórico `..._true.yml`. Não persista `MAINTENANCE_ENABLED=true` em
`backend/.env` (precedência: ambiente do shell > `.env` > default).

Verificação após qualquer recreate:

```bash
docker exec auneron-backend printenv MAINTENANCE_ENABLED     # esperado: false
docker logs auneron-backend 2>&1 | grep application_started  # ver campos abaixo
```

O **primeiro** `docker compose up` depois da adoção deste default recria o
backend (o hash de configuração muda). Isso é esperado.

## 5. Contrato estrito do floor

O `Settings` é deliberadamente tolerante (aceita, por exemplo, epoch numérico,
separador espaço, offset sem dois-pontos, sem segundos, valores futuros ou
obsoletos). **Por isso o preflight é obrigatório**: o procedimento exige:

* formato `YYYY-MM-DDThh:mm:ss[.ffffff]` seguido de `Z` ou `+hh:mm`/`-hh:mm`;
  ASCII, sem espaços, sem quebra de linha;
* calendário válido e **timezone obrigatório**;
* **não futuro** e **idade máxima de 30 minutos**, medidos contra o relógio do
  **PostgreSQL** (`clock_timestamp()`), nunca contra o relógio do host;
* forma canônica de registro: UTC com microssegundos e `Z`
  (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).

São **inválidos para o procedimento**: epoch (`0`, segundos, milissegundos),
número compacto, só data, datetime naive, separador espaço, `t`/`z`
minúsculos, offset sem dois-pontos, sem segundos, fração com mais de 6
dígitos, espaços/quebras de linha, dígitos não ASCII, texto, vazio, mês/dia/
hora inexistentes, valor futuro e valor com mais de 30 minutos.

Por que o relógio do PostgreSQL: `account_events.occurred_at` e
`work_items.created_at` usam `now()` do banco; o floor compara com esses
carimbos. Uma transação de pagamento iniciada **antes** do T0 e confirmada
depois tem `occurred_at < T0` e fica excluída (direção conservadora).

## 6. Ativação de uma janela (somente com autorização do PO)

Todos os comandos abaixo usam **placeholders**. Execute os passos 3, 4 e 5 no
diretório `backend/` com o **mesmo `DATABASE_URL` do alvo** e confirme que o
nome do banco impresso é o alvo pretendido.

1. **Pré-condições.** Autorização do PO registrada; SHA pinado `<SHA>` com CI
   `success`; árvore limpa (`git status --porcelain` vazio).
2. **Build e proveniência — depende de D-2 (bloqueado até lá).** Build a
   partir de um worktree limpo do `<SHA>`; `dirty=true`, `git_sha` `unknown` ou
   `invalid`, divergência de SHA ou de conteúdo, `extras != 0` ou container com
   image ID diferente do artefato verificado **reprovam a ativação**.
3. **Capturar T0** (somente leitura):

   ```bash
   python scripts/evidence_floor_preflight.py capture
   # saída: T0=<T0-canonico-UTC-Z> e clock_source=postgresql database=<alvo>
   ```

4. **Validar T0** (relógio do PostgreSQL; exit 0 = OK, exit 2 = reprovado,
   exit 3 = banco indisponível — fail-closed):

   ```bash
   python scripts/evidence_floor_preflight.py check <T0-canonico-UTC-Z>
   ```

5. **Recreate em até 15 minutos** após a captura (senão, recapture), passando
   o floor **no mesmo comando**. Use o compose DEV **sem** override em
   `%TEMP%`:

   ```bash
   ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=<T0-canonico-UTC-Z> \
     docker compose -f docker-compose.yml up -d --no-build backend
   ```

   ```powershell
   $env:ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR = "<T0-canonico-UTC-Z>"
   docker compose -f docker-compose.yml up -d --no-build backend
   ```

6. **Pós-verificação (tripwires).**
   * `docker exec auneron-backend printenv ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR`
     igual ao T0 e `printenv MAINTENANCE_ENABLED` = `false`;
   * linha `application_started` com `maintenance_enabled=false`,
     `evidence_worker_enabled=true`, `evidence_floor` = T0,
     `evidence_floor_state=armed` e `evidence_floor_age_seconds` pequeno
     (gap T0→start);
   * contagens de `approval_requests` e `authenticated_advisory_proposals`
     **inalteradas** (prova de que nenhum agente global acordou);
   * linhas históricas do incidente R3 e a conta 22 **inalteradas**.
7. **Registro de ativação** (seção 8), fora do repositório público se
   contiver identificadores.
8. **Encerramento da janela.** Remover a variável do floor e recriar; registrar
   `T_end`. Abortar e fechar a janela se qualquer tripwire falhar.

## 7. Fronteira analítica da primeira janela (congelada em 3.4C)

* **Coorte:** episódios cujo WorkItem de escalonamento nasceu **em ou após**
  T0 (o worker pode, tecnicamente, observar pagamento pós-T0 de episódio
  histórico; isso não entra na coorte).
* **Horizonte H = 30 dias.** Episódio maduro = idade ≥ H.
* **Taxonomia (protocolo analítico, não regra de negócio):** `OBSERVED_FACT`,
  `PAYMENT_NOT_YET_OBSERVED`, `PENDING_WITHIN_HORIZON`,
  `NO_PAYMENT_WITHIN_HORIZON`, `ABSTAINED_PAYMENT_BEFORE_EPISODE`,
  `UNVERIFIED_DUE_DATE_DIVERGENT`, `UNVERIFIED_MULTIPLE_PAYMENTS`,
  `UNVERIFIED_STATE_WITHOUT_EVENT`. `UNVERIFIED_*` = insuficiência para
  classificar (não é resultado negativo); `NO_PAYMENT_WITHIN_HORIZON` é
  observação descritiva após H (não prova que o escalonamento falhou).
* **Suficiência para análise descritiva** (nunca eficácia causal): C1 ≥ 30
  episódios maduros (alvo 50); C2 ≥ 10 `observed_fact` em ≥ 8 contas
  distintas; C3 partição com resíduo 0, ≥ 10 negativos e `UNVERIFIED_*` ≤ 10%;
  C4 ≥ 28 dias, ≥ 4 semanas ISO e nenhuma semana > 40%; C5 ≥ 90% limpos com
  proveniência verificável. Se o venue tiver volume estruturalmente
  incompatível, **não reduza números em silêncio**: volte ao checkpoint.
* **Fora da janela:** proposals/approvals históricos do
  `agent:OverdueDetectionAgent` (incidente R3) e a conta 22 — preservados,
  nunca apagados nem normalizados. `human_assessment` é reportado à parte
  (nunca somado a `observed_fact`).

## 8. Registro de ativação (modelo — todos os campos são placeholders)

| Campo | Valor |
|---|---|
| T0 (UTC, `Z`) | `<T0>` |
| Início do container (`StartedAt`) | `<STARTED_AT>` |
| Gap T0 → start (s) | `<GAP>` |
| SHA do código (40 hex) | `<SHA>` |
| Image ID verificado | `<IMAGE_ID>` |
| Venue (identificador, sem dados de cliente) | `<VENUE_ID>` |
| Operador / procedimento / autorização do PO | `<OPERADOR>` / este documento / `<REF_AUTORIZACAO>` |
| Estado dos gates no `application_started` | `<maintenance_enabled>`, `<evidence_worker_enabled>`, `<evidence_floor_state>` |
| Arquivos compose usados (+ hash) | `<ARQUIVOS_E_HASH>` |
| Baseline de linhas (contagens/ids históricos) | `<BASELINE>` |
| Horizonte H / T_end planejado | `30 dias` / `<T_END>` |

## 9. Segurança e privacidade

* Nenhum segredo, URL com credencial, e-mail, nome de cliente ou identificador
  de conta/episódio entra em log de ativação, neste documento ou no
  repositório.
* O log `application_started` expõe apenas escalares técnicos
  (`maintenance_enabled`, `evidence_worker_enabled`, `evidence_floor` em UTC,
  `evidence_floor_state`, `evidence_floor_age_seconds`,
  `evidence_interval_seconds`, `evidence_batch_size`).
* `evidence_floor_age_seconds` é diagnóstico, não decisão de negócio.
