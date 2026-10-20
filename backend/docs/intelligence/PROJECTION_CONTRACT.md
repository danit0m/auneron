# Projection foundation (INT-IMPL-2A)

Local, deterministic and inert. This package decides **what a model may be
offered** when it is asked for a plan: the subset of the capability registry
that one synthetic initiator may use, rendered as a JSON Schema plus a frozen
instruction text. A projection is **not an authorisation**: it only narrows,
it never adds, enables or grants anything, and the full validator of the
planning package remains the only authority on admission.

It has **no gateway call**, no provider, no route, no permission, no table,
no migration, no executor and no import by `app/main.py`. It lives in
`app/intelligence/proposal/` and imports only the standard library,
`jsonschema` and six modules of the planning package.

## Layout

| Module | Role |
|---|---|
| `contracts.py` | `proj.v1` constants, the frozen instruction text, enums, requests, `Projection`, `ProjectionResult`. |
| `digests.py` | Domain-separated digests: policy, schema, instructions, projection. |
| `rendering.py` | Sub-registry schema, pins, ASCII rule, size limit, vocabulary scanner. |
| `selection.py` | Capability candidates, filters and the prerequisite closure. |
| `builder.py` | Request validation, snapshots, `project`, `verify_projection`. |

## Contract (`proj.v1`)

`project(request) -> ProjectionResult` and `verify_projection(projection,
request) -> tuple[Rejection, ...]` are **total**: whatever they receive they
return a typed result and never raise an `Exception`. A hostile or malformed
input is a rejection; an unexpected internal failure is reported as
`projection_internal_error`, which the tests treat as a defect.

`ProjectionRequest` carries the full registry and its pinned digest, the
injected `PolicyReference`, the injected synthetic authority context, a
`ProfileView` (plain data: name and output-token cap, no gateway object),
`objective_ref`, `plan_version`, 1 to 8 `account_refs` and `ProjectionLimits`
(`max_schema_bytes` 12288, `max_account_refs` 8).

`Projection` is immutable and keeps **only primitives** (strings, integers,
tuples and the canonical JSON text of the schema). It holds no reference to
the registry, policy or authority it was built from, so changing those objects
afterwards cannot alter it. `output_schema` is a property that parses the
canonical text into a **new dict on every access**. `synthetic_authority` is
always `True`, `is_grant` and `replay_protected` always `False`.

## Flow

Fail-fast between stages, one outcome per stage.

| Stage | Name | Rejections |
|---|---|---|
| 0 | request | `request_invalid` |
| 1 | registry | `registry_integrity_failure` |
| 2 | authority | `authority_context_invalid` |
| 3 | policy | `policy_reference_missing`, `policy_reference_invalid` |
| 4 | scope | `scope_inaccessible` |
| 5 | selection | `projection_empty` |
| 6 | rendering | `schema_invalid`, `schema_too_large`, `projection_text_invalid`, `internal_term_leak` |
| 7 | internal | `projection_internal_error` |
| 8 | verification | `projection_malformed`, `projection_tampered`, `projection_stale` |

Foreign objects are read **once** and copied. The registry is rebuilt from
plain JSON copies: the `input_schema` and `output_schema` of every definition
go through the strict canonical form of the planning package and are parsed
back into containers made only of `dict`, `list`, `str`, `int` and `bool`.
The copy keeps no subclass and shares no mutable container with the original
registry, and unexpected types (tuples, sets, floats, `null`, non-text keys,
text that is not NFC) are refused instead of being coerced. `PlanLimits` is
recreated from its fields, which must be exact `int` values. The digest of
**that copy** is compared with the pin: the cached digest of the original
proves nothing about what it holds now. Any failure to build the copy, and
any difference from the pin, is `registry_integrity_failure`; every later
step uses only the copy. The authority context is revalidated (its
constructor runs again, including the refusal in production) and copied; any
failure, including a canonicalisation error in its fields, is
`authority_context_invalid`.

## Selection

Only capabilities that are `registered`, `enabled`, `llm_proposable` and not
`executable` are candidates. Everything else is invisible: it is not
projected and not even listed as excluded. For each candidate the **first**
failing filter decides the reason, in this order:

1. permissions: `required_permissions` must be a subset of the context's;
2. scope: a read capability needs every reference readable, a mutating one
   needs every reference manageable;
3. policy: `reconcile_capability` against the injected policy must be empty.

A reference that is unknown **or** not accessible rejects the whole request
with the same code (`scope_inaccessible`): no silent narrowing, no way to tell
the two apart. A prerequisite closure then removes any capability whose
`any_of` evidence capabilities are not offered (for example
`account.mark_overdue` without `nba.evaluate` or `action_space.evaluate`),
because no plan could ever satisfy the validator with it. An empty result is
`projection_empty`.

## Schema

The schema is built by the planning package from a **sub-registry** made of
the selected definitions (same version and limits, definitions shared, not
copied). The sub-registry exists only inside the call: its digest is never
used, and it is never given to a validator. `objective_ref` and
`plan_version` are pinned as `const`, so the model cannot choose them; the
pipeline must still check that equality after validation, because the
validator does not know the intent.

The schema is checked as a Draft 2020-12 schema, must be closed (no open
object), printable ASCII, at most `max_schema_bytes` canonical bytes, and free
of internal vocabulary. The full registry schema is 6267 canonical bytes
(6222 with the pins).

## Instructions

The text is part of the versioned contract (`plan-instructions.v1`, 279
bytes, printable ASCII, SHA-256
`ff48f1c23aecca5b67548c2845df0b7dbf2165af9c271b9212c1d8448fdf87bb`):

> Return exactly one JSON object that conforms to the provided JSON Schema.
> Use only the capabilities present in the schema and do not add fields that
> the schema does not define. Do not include digests, server-generated
> identifiers, credentials or any text outside the JSON object.

It carries no data of the request. Any literal change needs a new approval, a
new template id and a new pin.

## Vocabulary policy

Internal **values** are forbidden, generic language is not.

* Compound identifiers (containing `_`, `.`, `:` or `/`) of every capability
  that is **not** projected, action references, corridors, separation tokens,
  approval kinds, every permission string and the compound names of the
  digest fields. A string equal to a projected capability id is public.
* Schema keys are compared exactly with a fixed list of internal field names
  (`executable`, `enabled`, `registered`, `corridor`, ...). A key is not
  prose, so plain words are covered here.
* Opaque runs: 32 or more hexadecimal characters or 40 or more base64-like
  characters.

Comparison is by case-folded substring over printable ASCII text, so
homoglyphs, full-width forms, zero-width characters and decomposed accents
are rejected by the ASCII rule before they could be compared. Words such as
"digests" and "credentials" are generic and allowed.

## Digests

`registry_digest` (`auneron.capreg.v1`) and `context_digest`
(`auneron.plan.v1.context`) come from the planning package unchanged. New
domains: `auneron.proj.v1.policy`, `.schema`, `.instructions`, `.projection`.
All nine domains are distinct, newline-terminated and none is a prefix of
another.

`projection_digest` covers the contract, template id, registry digest,
context digest, policy digest, profile (name and cap), both sets of limits,
objective, plan version, sorted references, sorted capability ids, sorted
exclusions, schema digest and instructions digest. It identifies and checks
integrity. It is **not** protection against replay: `replay_protected` is
always `False` and durable replay protection needs persistence, which does
not exist in this phase.

## verify_projection

An empty result means: well formed, internally consistent and exactly what the
request produces now. Otherwise exactly one code:

* `projection_malformed`: wrong shape or types, extra or missing attributes,
  a flag that is not the constant value;
* `projection_tampered`: its own digests do not match its own content;
* `projection_stale`: consistent, but the request no longer yields it
  (authority, policy, registry, scope or any other input changed, or the
  request is no longer valid).

## Limits and budget (informative)

The gateway preflight counts instructions and input text only, but the
reservation also counts the schema at one token per byte. The projected schema
is therefore bounded by `max_schema_bytes`. Current lab profiles allow 512 and
1024 output tokens; a typical three-step plan is about 1 KB and a maximum
eight-step plan about 7 KB. This phase documents these numbers and changes no
profile or price.

## Known defects of the planning package

Found while designing this package and **not fixed here**; this package only
mitigates them:

* D1: `validate_plan` propagates `CanonicalError` when the authority context
  has non-canonical text. The projection reports `authority_context_invalid`.
* D2: the registry digest is cached at construction, so an in-place change of
  a definition is not noticed by the pin check. The projection recomputes the
  digest on a plain JSON copy of the registry (see Flow) and compares that
  digest with the pin.
* D3: malformed server-side objects raise untyped exceptions from the
  planning API. The projection catches them and returns typed rejections.

A corrective increment of the planning package must come before the pipeline
integrates the validator.

## What this is not

No gateway call, no provider schema adaptation, no executor, no approval, no
persistence, no replay protection, no route, permission, table or migration.
A later, separately approved step wires this projection to the gateway and
validates the model's answer with the full validator and the full registry.

## Tests

`tests/test_intelligence_projection_contracts.py`, `_selection.py`,
`_schema.py`, `_digests.py`, `_security.py` and `_boundaries.py`, plus the
exact file lists in `tests/test_intelligence_boundaries.py` and the scoped
exemption in `tests/test_intelligence_plan_boundaries.py`. Run them through
the standard backend procedure (`scripts/test.ps1`).
