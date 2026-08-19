# Agent Skills Threat Model V1

**Status:** Finalized through Commit 23E

## 1. Protected assets

- stable skill identity and publisher attribution;
- versioned implementation and data contracts;
- declared resource requirements;
- deterministic agent bindings;
- future invocation inputs, outputs and audit history;
- tenant data reachable through a skill.

## 2. Threats and 23A controls

| Threat | 23A control |
| --- | --- |
| key spoofing or ambiguous identity | canonical unique `skill_key` |
| silent implementation replacement | exact version plus manifest digest |
| floating agent behavior | binding pins `skill_version_id` |
| undeclared resource use | normalized capability declarations |
| unbounded runtime contract | timeout and output ceilings in manifest data |
| lifecycle corruption | status/timestamp database constraints |
| history loss after user removal | audit foreign keys use `SET NULL` |
| destructive catalog deletion | version and binding `RESTRICT` rules |
| capability mistaken for permission | architecture and schema separate declaration from authority |

## 3. Additional 23B controls

| Threat | 23B control |
| --- | --- |
| client-selected or ambiguous digest | server-calculated canonical contract digest |
| partial publication | one service-owned transaction with row locks |
| silent replacement of published behavior | published contract and capabilities are immutable |
| binding to draft or retired behavior | service requires active skill and exact published version |
| secrets stored in catalog JSON | bounded recursive sensitive-key rejection |
| race on identity or lifecycle | row locks plus named uniqueness constraints |

## 4. Additional 23C controls

| Threat | 23C control |
| --- | --- |
| catalog text triggers arbitrary code import | exact `(runtime_kind, handler_reference)` must exist in explicit `SkillHandlerRegistry` allowlist |
| remote schema reference performs network access | non-local `$ref` is rejected before validation |
| invalid or oversized input | canonical JSON, 64 KiB/depth bounds and Draft 2020-12 validation |
| duplicate mutating/external execution | mandatory idempotency key plus database uniqueness and request fingerprint |
| concurrent duplicate execution | `running` ledger is committed before handler submission |
| unbounded executor queue | fixed worker pool plus non-blocking `BoundedSemaphore` |
| handler runs beyond caller budget | published timeout records `timed_out`; slot stays reserved until callable exits |
| invalid or oversized handler output | canonical byte ceiling plus published output-schema validation |
| exception detail leaks | generic domain errors and bounded persisted error codes |
| raw request retained in execution history | ledger stores input digest/fingerprint, not raw input |
| invocation history lost with catalog deletion | version-to-invocation foreign key uses `RESTRICT` |

## 5. Additional 23D controls

| Threat | 23D control |
| --- | --- |
| unauthenticated invocation | service API key plus authenticated user session |
| actor spoofing from request JSON | HTTP actor is derived only from `AuthenticatedSession` |
| role exceeds execution class | separate RBAC for read-only, mutating and external execution |
| account/user IDOR | scope identifiers are authorized before runtime; inaccessible resources return opaque not-found |
| authorized ID differs from executed ID | reserved account/user identifiers live in the exact runtime `input_payload` |
| undeclared reserved scope identifier | reserved identifier without matching capability is rejected |
| external action after stale authentication | external execution requires a currently elevated session |
| capability declaration treated as permission | policy independently checks every declaration against RBAC and resource scope |
| public ledger leaks cross-scope history | 23D exposes no invocation-history endpoint |
| HTTP/parser error leaks implementation detail | skill-specific frozen errors, no-store responses and sanitized failures |

## 6. Additional 23E controls

| Threat | 23E control |
| --- | --- |
| Skill request flood by an authenticated actor | bounded per-user sliding-window limiter plus existing bounded executor |
| rate-limit state reveals caller identity | limiter keys are SHA-256 identities and are never emitted |
| telemetry leaks input, idempotency keys or exception text | dedicated Skill telemetry emits only bounded operational metadata |
| process crash leaves permanent `running` ledger rows | stale rows are recovered in locked batches after a threshold above maximum published timeout |
| multiple workers recover the same stale row | `FOR UPDATE SKIP LOCKED` plus service-owned transaction |
| stale recovery replays a possibly side-effecting handler | recovery never executes the handler; it only terminalizes the ledger |
| timeout storm creates unbounded work | timed-out callable retains its semaphore slot until it actually exits |
| hard-kill claim hides continuing side effects | operations contract explicitly states thread timeout is not cancellation |

## 7. Residual constraints after Commit 23

| Constraint | Required boundary |
| --- | --- |
| in-process rate limiter is not distributed across replicas | reverse-proxy/gateway or shared distributed limiter in production |
| Python thread cannot be safely hard-killed | separate process/container execution before untrusted executable plugins are enabled |
| sensitive-action approval and autonomy | Commit 24 |
| autonomous non-user actor authority | Commit 24 |

## 8. Non-authority rule

The following can describe requested behavior but never authorize it:

- skill manifest or capability rows;
- agent name or binding configuration;
- Work Manager item, comment or linked Memory;
- model-generated plan, tool call or confidence score;
- plugin-provided text or external response.

An authenticated actor, RBAC/scope policy and—when introduced—an explicit
approval decision remain independent prerequisites.

23C actor fields remain audit attribution only. For the 23D HTTP API, user actor
fields are generated only after authentication and authorization; callers still
cannot turn an actor field, Work item, Memory item or capability row into
authority.

## 9. Secrets

Manifests, schemas and binding configuration must not contain credentials,
tokens or connection strings. Commit 23 does not introduce a secret store.

The 23C ledger intentionally does not persist raw invocation input or raw
exception messages. Successful output is retained only because exact
idempotent replay requires it; 23D/23E remain responsible for authorizing and
classifying what data may enter or leave a skill.
