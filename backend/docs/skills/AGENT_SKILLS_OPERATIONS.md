# Agent Skills Operations

**Status:** Gate 23E / Commit 23 final operating contract

## 1. Purpose

This document defines the operational controls around the trusted in-process
Agent Skills runtime. It does not grant authority. Authentication, RBAC,
resource scope and future approvals remain independent prerequisites.

## 2. Safe telemetry

Skill runtime events may contain:

- request ID when one exists;
- invocation ID and exact skill version ID;
- actor type, but not actor reference;
- lifecycle status and duplicate flag;
- duration and successful output byte count;
- sanitized bounded error code.

They must not contain raw input, raw output, idempotency keys, credentials,
cookies, authorization headers, raw exception messages or tracebacks.

## 3. Abuse control

The explicit HTTP API applies a per-user sliding-window limiter before catalog
authorization/runtime work. The limiter stores only a SHA-256 user identity.

Environment controls:

```text
SKILL_RATE_LIMIT_USER_MAX_REQUESTS=60
SKILL_RATE_LIMIT_WINDOW_SECONDS=60
```

This limiter is process-local defense-in-depth. Multi-worker or multi-replica
production must also enforce a distributed limit at the reverse proxy, gateway
or shared rate-limit service.

## 4. Runtime capacity

```text
SKILL_RUNTIME_MAX_WORKERS=4
```

The worker pool remains bounded. A timed-out callable keeps its semaphore slot
until the callable actually returns. This prevents timeout storms from creating
an unbounded executor queue.

## 5. Stale-running recovery

A process crash can leave a committed invocation in `running`.

```text
SKILL_STALE_RUNNING_SECONDS=600
SKILL_RECOVERY_INTERVAL_SECONDS=60
SKILL_RECOVERY_BATCH_SIZE=100
```

The stale threshold cannot be configured below 301 seconds because a published
Skill timeout can be as high as 300 seconds.

Recovery:

1. selects only `running` rows older than the cutoff;
2. locks a bounded batch with `FOR UPDATE SKIP LOCKED`;
3. never calls the handler;
4. marks each row `failed`;
5. stores only `stale_running_recovered`;
6. sets terminal timestamps/duration;
7. commits once from the service boundary.

This is ledger recovery, not proof that a crashed handler had no side effect.

## 6. Timeout and isolation boundary

Python cannot safely hard-kill an already-running thread. Commit 23 therefore
does not claim that timeout cancels execution or rolls back side effects.

Only trusted handlers explicitly registered in `SkillHandlerRegistry` may run
inside the process. Before executable untrusted plugins are enabled, production
must introduce a separate process/container execution boundary with enforceable
termination and resource isolation.

## 7. Commit 24 boundary

Commit 24 adds approval/autonomy policy. Rate limits, recent authentication,
idempotency and runtime containment do not replace approval for sensitive
actions.
