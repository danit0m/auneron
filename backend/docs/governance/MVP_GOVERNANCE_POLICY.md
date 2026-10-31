# MVP Governance Policy

```
Status:      PROPOSED_PENDING_HUMAN_ACCEPTANCE
Prepared by: Claude (product developer), 2026-10-30, at tree f1e641d4
             Amended (staged, not applied): TASK041, replay and DELETE contract
Accepted by: — (none; this document has no effect until a named human
             records acceptance in a separate, dated record)
```

This document records the PO-recommended choices for decisions D1-D5.
It is a proposal. It adds no new technical claim, enables nothing in
production, issues no grant, and does not close historical incident H1.

## 1. Scope and references

Governed actions in the MVP: `account.mark_overdue` (human corridor) and
`account.mark_paid`. Their conformance is the one already recorded in
`backend/docs/GOVERNED_ACTION_MODEL.md` §6 and §11 (N0-N3, both corridors).
This policy re-uses that evidence; it does not re-prove it.

UP-2 (authority convergence at request/approval creation,
`GOVERNED_ACTION_MODEL.md` §9) is **deferred and out of the MVP**.

## 2. D1 — Account identity v1 (conservative)

- Identity v1 = normalized `cliente` (whitespace-collapsed, casefold),
  `valor` (exact decimal), `vencimento` (date).
- Scope limit: **one company per installation, one application replica**.
  Enforcement is application-level under one global PostgreSQL advisory
  lock; there is no database unique constraint. Outside this scope the
  rule is not claimed to hold.
- Effect on all identity writers:
  - `POST /accounts`: collision → 409, nothing written.
  - `PUT /accounts/{id}`: change to cliente/valor/vencimento colliding
    with another account → 409.
  - Import confirmation: in-file duplicate blocks the whole file;
    collision with an existing account → 409, nothing written.
  - Legacy CSV upload (`POST /upload/`): colliding row is **skipped** and
    counted in `duplicados` (not 409). Operators must read that counter.
- Two legitimate titles with the same client, value and due date are
  **not representable** under v1. The pilot entry sample (area5) must
  contain **zero** legitimate collisions; otherwise the pilot does not
  start.
- No bypass: names, values or dates must not be altered to get past the
  rule.
- External title identity (document number/version) is **not implemented
  now**. It requires a separate decision and its own DDL/Git gates.

## 3. D2 — Human separation of duties

- The approver must be a **named human distinct** from the requester and
  from the executor. Requester and executor may be the same person
  (GAM UP-1). The code enforces distinct user IDs; distinct humans are an
  operational rule enforced by this policy.
- No shared credentials. One account per human; no account held by two
  people.
- Credential reset: no approved reset mechanism exists today
  (`CUSTOMER_DEPLOYMENT_RUNBOOK.md`, "Limitação de password recovery";
  `reset_test_password.py` is untracked and not approved). A reset may
  only happen through an admin procedure that is written and recorded
  before use. No operational reset may be used to act as, or obtain the
  credentials of, another role holder (in particular, to approve).

## 4. D3 — Data-entry writes outside the governed action set

`POST`/`PUT`/`DELETE /accounts`, import confirmation and the legacy CSV
upload are **RBAC-gated data-entry writes outside the GAM V1 action set**.
They cannot change `status`: `AccountCreate`/`AccountUpdate` have no
`status` field, and the legacy upload forces `aberto`.

Actual audit coverage at tree `f1e641d4` (static reading; **not all manual
operations are audited**):

| Write | Durable audit record |
|---|---|
| Import confirmation | Receipt + `import_confirmation_audit_events` (actor, key, content hash, account refs) |
| `PUT` changing `vencimento` | `account_vencimento_changes` (actor, previous/new) |
| `PUT` changing cliente/valor/email/whatsapp | **None** |
| `POST /accounts` | **None** durable; a best-effort advisory proposal whose failure is swallowed |
| `DELETE /accounts/{id}` | **None** for the delete itself. With the staged TASK041 guard (not applied to the repo): under the identity lock and a `FOR UPDATE` lock on the account row, an account referenced by any `account_events`, `account_vencimento_changes`, `approval_requests.target_account_id`, `policy_authority_consumptions.target_account_id` or `business_effect_verifications.target_account_id` row gets 409 and nothing is deleted. Accounts without such history are still hard-deleted (204), with no record. Existing `RESTRICT` references (memory item, NBA snapshot, work item) still block at the DB as before |
| Legacy CSV upload | **None** (no actor recorded; only the HTTP response summary) |

Closing these gaps is a separate decision, not part of this policy.

## 5. D4 — Autonomy level for the MVP

- MVP = **L2, human-supervised**, for the two actions above only.
- The L3 policy worker for `account.mark_overdue` stays **not activated**:
  no fresh grant is issued by this policy or by any MVP step.
- This does not remove or deny the historical L3 capability recorded
  elsewhere; it only keeps it inactive.

## 6. D5 — Meaning of `mark_paid`

`account.mark_paid` is a **human assertion** that payment happened. It is
not bank proof. `payment_observed` remains unproven (GAM UP-3).

Replay of a consumed `mark_paid` approval (staged TASK041 contract, not
applied): the stored receipt is returned (`duplicate=true`, no write) only
to the original executor (`authority_user_id` of the consumption), for the
same approved `account_id` and `expected_status`, while that user is still
active and still passes the current authorization check, and only if the
receipt chain (decision, input digest, fingerprint, runtime key,
invocation, output) is consistent and the account is `pago`. The approver,
other users, another account or another expected status are rejected
(403/409) without the receipt. Expiry of the approval after consumption
does not invalidate the receipt.

## 7. Authority provenance

- An agent-generated JSON file or receipt is transport, **never human
  consent**.
- Any mutable DB, migration, deploy, grant or credential action needs
  verifiable human approval given **before** execution and bound to the
  operation, target and scope.
- Product developer and reviewer are separate roles. Any exception is
  recorded explicitly; it is never inferred or granted retroactively.

## 8. Open items (not closed by this document)

- Human acceptance of D1-D5 (named human, dated record).
- H1 (28/10 mutable execution on `auneron_test` without prior verifiable
  consent): needs the user's own dated statement and a PO closure record.
  No backdating.
- H2 (PO-authored schema/migration code): exception record by PO.
