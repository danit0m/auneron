# Arquitetura do Auneron

## Visão geral

O Auneron está organizado em três camadas principais:

```text
Frontend React/Vite
        |
        | HTTP /api
        v
FastAPI + agentes/orquestrador
        |
        | SQLAlchemy / Psycopg
        v
PostgreSQL 17
```

## Frontend

Responsabilidades:

- navegação e apresentação;
- controle de permissões da interface;
- dashboards, clientes, Brain e centro executivo;
- geração e propagação de `X-Request-ID`;
- tratamento de falhas HTTP;
- integração com a API somente por URLs relativas `/api`.

O controle de permissões no React melhora a experiência de uso, mas não
deve ser considerado uma barreira de segurança suficiente para dados ou
operações sensíveis.

## Borda HTTP

### Desenvolvimento

```text
Browser -> Vite /api -> FastAPI
                    + X-API-Key
```

O processo Node do Vite conhece a API key; o bundle React não.

### Produção

```text
Browser -> HTTPS -> Nginx/reverse proxy -> FastAPI
                                      + X-API-Key
```

A API key permanece no lado servidor.

Essa chave é uma credencial de serviço. Ela não substitui autenticação
individual de usuário.

## Backend

O FastAPI contém:

- endpoints públicos de infraestrutura;
- routers de negócio protegidos por `X-API-Key`;
- serviços de aplicação;
- agentes e orquestrador;
- SQLAlchemy;
- configuração por ambiente;
- observabilidade estruturada;
- headers HTTP de segurança;
- rate limiting de login e elevação;
- liveness em `/health` e readiness em `/ready`;
- manutenção periódica de sessões expiradas/revogadas.
- Work Manager com serviço transacional, RBAC por operação, escopo
  global/conta/usuário e histórico de eventos append-only.

A aplicação testa a conectividade com o banco no startup e libera o pool
no shutdown. O resultado da checagem inicial é registrado, mas `/health`
permanece independente do banco; disponibilidade do PostgreSQL é refletida
por `/ready`.

## Banco de dados

Persistência principal:

```text
PostgreSQL 17
```

Evolução de schema:

```text
Alembic
```

O SQLite presente como artefato histórico não é a persistência principal
do runtime atual.

A aplicação utiliza pool de conexões com limites e timeouts definidos por
configuração. Migrações Alembic utilizam conexões separadas do pool da API.

## Ambientes

Ambientes reconhecidos pela configuração incluem desenvolvimento, teste e
produção.

Proteções relevantes:

- o ambiente de teste só pode utilizar o banco `auneron_test`;
- o banco `auneron_test` não pode ser usado como banco normal;
- produção exige PostgreSQL e configuração explícita da segurança;
- produção rejeita API key fraca/placeholder, debug e echo de SQL;
- CORS de produção aceita somente origens HTTPS explícitas quando cross-origin;
- segredos não devem existir no código-fonte ou no bundle React.

## Testes

A suíte possui dois níveis principais.

### Backend

Pytest valida endpoints, banco, constraints, segurança, configuração,
observabilidade, rate limiting, liveness/readiness e manutenção de sessões.

### E2E

Playwright abre um navegador real e valida:

```text
Browser -> Vite -> FastAPI -> PostgreSQL de teste
```

O E2E utiliza `auneron_test`.

## Observabilidade

Toda requisição possui `X-Request-ID`.

Logs HTTP estruturados incluem, entre outros:

- método;
- caminho;
- status;
- duração;
- ambiente;
- request ID.

Credenciais, URLs de banco, cookies, tokens e outros campos sensíveis
devem permanecer mascarados.

Eventos de autenticação registram sucesso/falha, elevação, logout e excesso
de tentativas sem incluir senha, token bruto, cookie ou API key.

Mutações do Work Manager registram somente metadados operacionais do evento e
o resultado aplicado/repetido, correlacionados pelo request ID. Conteúdo do
trabalho, comentários, contexto e chaves idempotentes não entram nesses logs.

## Limites atuais

A arquitetura atual implementa autenticação individual real de
usuário no backend.

O navegador autentica por `POST /auth/login` e recebe um cookie de sessão
`HttpOnly`. O token bruto não é exposto ao JavaScript; o PostgreSQL armazena
somente o hash do token.

A `X-API-Key` permanece como credencial de serviço entre Vite/Nginx e
FastAPI. Ela não identifica o usuário e, sozinha, não concede acesso às
rotas de negócio.

O FastAPI aplica RBAC server-side às rotas protegidas. Operações
administrativas sensíveis exigem também uma elevação temporária vinculada
à sessão atual.

O frontend replica a matriz de permissões para navegação e experiência de
uso, mas a fronteira efetiva de segurança permanece no backend.

O rate limiter de autenticação atual é em memória e por processo. A topologia
atual utiliza um processo backend; antes de escalar horizontalmente, o limiter
deve migrar para um armazenamento compartilhado.

O `backend/docker-compose.yml` é uma topologia local de desenvolvimento.
Produção deve usar HTTPS, segredos gerenciados e PostgreSQL não exposto
publicamente.

## Legacy Orchestrator observe-only quarantine

Commit 25E places the pre-Work Orchestrator execution plane behind a fail-closed
quarantine. `EventBus.publish` now uses `AIOrchestrator.observe`, which preserves
Decision Engine evaluation, the existing in-memory DecisionStore and candidate
agent resolution, but stops before `ExecutionPipeline` or any legacy
`agent.handler(payload)` call.

`AIOrchestrator.execute`, `ExecutionPipeline.execute` and
`ExecutionPipeline._execute_agent` remain compatibility symbols and immediately
raise `LegacyAutonomyExecutionBlockedError`. There is no configuration or
runtime bypass.

This boundary is intentionally separate from the governed Work/Skill runtime.
No authority user, role or scope is synthesized; decision output and selected
agent names are advisory data only. Commit 25E creates no Work, selects no Skill,
changes no Approval behavior and adds no automatic retry/replan. A future
Decision-to-Work proposal adapter requires its own authority and idempotency
design before implementation.

## Advisory Orchestrator-to-Skill binding projection

Commit 25F adds a SELECT-only internal projection from the quarantined legacy
Orchestrator's advisory `selected_agents` metadata to existing enabled
`AgentSkillBinding` records. Only published SkillVersions on active Skills are
eligible, and repository ordering (`priority ASC`, `id ASC`) is preserved.

The projection returns a bounded metadata allowlist and intentionally excludes
binding configuration, handler references, manifests, capabilities, input
payload, authority identity, Approval state, runtime context and Memory. A
projected binding is advisory metadata only: it grants no authority and is not
executable intent.

25F adds no production EventBus wiring and does not create Work, configure or
dispatch WorkSkillExecution, invoke governed Skill execution, mutate Approval or
Memory, or synthesize `authority_user_id`. A later bridge requires a separate
authority provenance and recovery contract before any action can be enabled.

## Authenticated authority provenance reference

Commit 25G adds an internal immutable `AuthorityProvenance` value derived only
from the server's existing `AuthenticatedSession`. It records the authenticated
user id, auth-session id, optional bounded request correlation id and a fixed
server source marker.

The reference grants no authority, is not an authorization decision and is not
executable intent. Role, permissions, scope, session elevation, Approval state,
Skill/binding selection, payload, runtime context, Memory, credentials and
tokens are intentionally excluded.

Any future bridge must reload the current User and AuthSession, confirm that the
session remains active, recalculate current role/permissions and reauthorize
scope and the exact Skill. Missing, expired, revoked or disabled authority fails
closed.

The first 25G APPLY has no production EventBus wiring, database access, Work
creation, Skill execution, Approval/Memory mutation, public API or schema
migration.

## Authenticated advisory context envelope

Commit 25H adds an internal immutable `AuthenticatedAdvisoryEnvelope` that
composes the existing `OrchestrationDecision`, `AdvisorySkillBindingPlan` and
`AuthorityProvenance` without changing any production execution path.

The envelope validates that `plan.decision_name` equals
`decision.decision_name` and that the ordered `plan.agents` names exactly match
`decision.selected_agents`. It grants no authority, is not an authorization
decision and is not executable intent.

Role, permissions, scope, session elevation, payload, runtime context, Work,
Approval, credentials, tokens and Memory are not copied into the envelope. Any
future consumer must reload current user/session authority and reauthorize
scope and the exact Skill, failing closed on missing, expired, revoked or
disabled authority.

The first 25H APPLY has no production EventBus or route wiring, no database
access, no Work creation, no Skill execution, no Approval/Memory mutation, no
public API and no schema migration.

## Authenticated advisory envelope assembly service

Commit 25I adds an internal non-routed assembly service that composes the
existing authenticated authority provenance, observe-only Orchestrator decision,
SELECT-only advisory Skill projection and immutable authenticated advisory
envelope.

The only authority source is the existing server `AuthenticatedSession`.
`authority_user_id` and `auth_session_id` cannot be supplied by the caller.
`event_name` is bounded non-blank text and `payload` must be a dictionary; both
are ephemeral inputs and are not copied into the envelope or persisted.

The assembly calls only `AIOrchestrator.observe`, never legacy execute or
EventBus publish. Its projection dependency is injected and remains SELECT-only.
The assembly module has no direct database Session dependency and creates no
Work, executes no Skill, mutates no Approval or Memory, exposes no production
route and changes no public API or database schema.

The returned envelope remains provenance-only advisory context. Any future
mutating consumer must reload current user/session authority, reauthorize
current scope and the exact Skill, and fail closed for stale, revoked, expired,
disabled or unauthorized authority.

## Authenticated advisory proposal durability (25J)

The internal `AuthenticatedAdvisoryProposalService` may durably snapshot an
already-authenticated advisory envelope under protocol
`authenticated_advisory_v1`. The proposal is immutable and non-executable.
Its idempotency identity is `authority_user_id + auth_session_id +
idempotency_key`; `request_id` remains correlation metadata only.

The snapshot stores only the decision name, ordered selected agents, and safe
ordered advisory Skill-binding metadata. It excludes reason, confidence,
signals, event payload/name, role, permissions, scope, elevation, credentials,
tokens, Work, Approval, Memory, and execution intent.

Durable authority IDs are provenance references without foreign-key retention
coupling and grant no authority. Every future mutating consumer must reload the
current user/session, reauthorize current scope and the exact Skill, and fail
closed before any action.

## Authenticated advisory proposal reauthorization validation (25K)

25K adds an internal, non-routed, SELECT-only consumer for one exact binding
candidate from a durable authenticated advisory proposal. The stored proposal
remains provenance-only and grants no authority.

The boundary reloads the current User and AuthSession, verifies exact
user/session identity and current session validity, reloads the exact current
binding/version/Skill, and calls `authorize_skill_execution` with the current
role, current server-derived session elevation, and the candidate's ephemeral
input payload. Missing, revoked, expired, disabled, stale, or unauthorized state
fails closed.

The returned frozen validation is not reusable authorization and does not
survive TOCTOU. Any later governed execution, Work or Approval mutation must
reauthorize again at its final action boundary.

25K has no runtime invocation, Work/Approval/Memory mutation, EventBus wiring,
public route, database write, row lock, schema migration, Alembic change, or
OpenAPI change.

## Authenticated advisory read-only governed dispatch (25L)

25L enables the first action path from one durable authenticated advisory
proposal candidate, while preserving the separation between advisory metadata
and live authority.

`AuthenticatedAdvisoryProposalDispatchService` accepts only `proposal_id`,
server-derived `AuthenticatedSession`, `binding_id`, and ephemeral
`input_payload`. It canonicalizes the input and calls the 25K
`AuthenticatedAdvisoryProposalConsumptionService.validate` inside the dispatch;
a caller cannot supply or reuse a prior validation result as authority.

Only candidates that remain exactly `read_only` + `internal_python` are
eligible. Actor attribution is derived server-side as
`agent:<validated agent_name>` and the runtime idempotency key is derived as
`advisory:<proposal_id>:<binding_id>`. The key makes one proposal binding
candidate one governed action identity: same-input retries replay, while
different-input retries conflict before a second handler execution.

The final action is delegated exclusively to
`GovernedSkillExecutionService.execute`, which reloads current authority and
Skill state, evaluates the existing low-risk autonomy policy, requires the
trusted isolated handler contract, and re-runs `authorize_skill_execution`
immediately before runtime. 25L does not call `SkillRuntimeService` directly.

Mutating, external, and plugin autonomous dispatch remain blocked. Approval
bridging, Work materialization, EventBus integration, public routes, schema
changes, Alembic changes, and OpenAPI changes remain separate checkpoints.
