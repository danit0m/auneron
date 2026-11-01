# MVP Governance Policy

```
Status:      PROPOSED_PENDING_HUMAN_ACCEPTANCE
Prepared by: Claude (product developer), 2026-10-30, at tree f1e641d4
Amended:     2026-10-31, TASK044 refresh at tree 2dc993cc (main 0aae4d0)
Amended:     2026-10-31, TASK045 data-entry audit code PREPARED_NOT_EXECUTED
             (staged outside the repository; not merged; tests not run;
             migration c3a8f1e6d204 not applied)
Amended:     2026-10-31, TASK046 refresh after the user's MVP scope
             expansion (stable title reference + assisted bank
             reconciliation); design only, no code (CONTRACT-046)
Amended:     2026-10-31, TASK047 audit recovery gate PREPARED (CI runner,
             offline tests and workflow staged outside the repository;
             proposed, not run; no PASS exists)
Accepted by: — (none; this document has no effect until a named human
             records acceptance in a separate, dated record)
```

This document records the PO-recommended choices for decisions D1-D5.
It is a proposal. It adds no new technical claim, enables nothing in
production, issues no grant, and does not close historical incident H1.
Operational messages such as "Continue" are not acceptance of this
document, of any decision D1-D5, or of H1/H2.

On 2026-10-31 the user (Daniel) included assisted bank reconciliation and
a stable title reference in the MVP and asked the PO to continue with its
recommendations (`PO-MVP-SCOPE-20261031-RECONCILIATION.md`,
`PO-SCOPE-DECISION-20261031-RECONCILIATION.json`). That record sets the
**scope** and adopts the PO-recommended direction for D2, D4 and D5 as MVP
scope. It is **not** a standalone named acceptance of D1-D5 or of this
document. It does not acknowledge H1. It does not authorize publication,
migration, database operations or deploy
(`execution_authorized_by_this_record=false`).

## 0. Code state this document describes

Merged to Git `main` and **not deployed to any production or customer
environment**:

| Change | Merge commit | CI (PR / post-merge) |
|---|---|---|
| PR #11: mark-paid replay binding, DELETE history guard, PUT status immutability test | `da1dbc3` | `41194134205` / `41195256315` |
| PR #12: human mark-overdue replay binding | `0aae4d0` | `41203778900` / `41205028535` |

CI run IDs are as recorded by the PO. They were not queried while
preparing this document. Both PRs were reported green on PR and after
merge.

Prepared, **not merged and not executed**: TASK045 (data-entry audit
table and legacy in-file duplicate fix, §2 and §4), status
`AUDIT_PREPARED_NOT_EXECUTED`. Nothing in this document claims that
TASK045 works until its tests have run and it has been merged.

Prepared, **not merged and not run**: TASK047, a proposed CI gate that
would prove only that the TASK045 audit table, its constraints, index,
function, trigger and one synthetic row survive a native PostgreSQL
dump/restore (§4). It has no result until an approved GitHub run passes.

Designed, **not implemented**: stable title reference (§2) and the
assisted-reconciliation transaction contract (§6), both in
`CONTRACT-046-TITLE-IDENTITY-AND-RECONCILIATION.md`.

## 1. Scope and references

Governed actions in the MVP: `account.mark_overdue` (human corridor) and
`account.mark_paid`. Their conformance is the one already recorded in
`backend/docs/GOVERNED_ACTION_MODEL.md` §6 and §11 (N0-N3, both corridors).
This policy re-uses that evidence; it does not re-prove it.

UP-2 (authority convergence at request/approval creation,
`GOVERNED_ACTION_MODEL.md` §9) is **deferred and out of the MVP**.

Scope limit (G4): every guarantee here is claimed for **one company per
installation and one application replica**. Multi-replica operation is
not claimed.

Area order (PO): area1 closed (accepted baseline); **area2 is the only
active area**; then area3 (M1/shadow), area4 (journey + assisted
reconciliation), area5 (pilot readiness).

## 2. D1 — Account (title) identity

**Direction (user-requested scope, implementation pending):** a stable
title reference is an MVP requirement. The earlier proposal to use
cliente + valor + vencimento as the **definitive** identity is
**superseded**. It was never accepted, and it is not final policy.

Target (CONTRACT-046 §A, design only):

- `accounts.id` remains the stable internal ID.
- An optional origin reference (`title_source`, `title_reference`) is
  unique per source, scoped to the installation's single company, and
  enforced by a database unique index.
- Titles without a reference (all current data) stay valid as
  "unreferenced". They are not merged, deleted or given invented
  references.
- Two legitimate titles with the same client, value and due date are
  representable when their references differ. The tuple then only
  produces a warning.
- When either title is unreferenced, the tuple collision stays a hard
  conflict. A row with insufficient identity goes to review or mapping;
  it is never inserted silently.
- Import identity v1 receipts (content digest + actor/key) remain
  replayable and protected.
- Import v2 puts origin and reference in its semantic digest.
- Manual creation retries are covered by operation idempotency, which is
  a separate guarantee from title identity.

**Current code (unchanged at `0aae4d0`): conservative fallback, v1.**
This is how the code behaves today. It is not the final policy.

- Identity v1 = normalized `cliente` (whitespace-collapsed, casefold),
  `valor` (exact decimal), `vencimento` (date).
- Enforcement is application-level under one global PostgreSQL advisory
  lock; there is no database unique constraint. Outside the G4 scope the
  rule is not claimed to hold. On a non-PostgreSQL database the
  normalized check is not applied.
- Effect on all identity writers:
  - `POST /accounts`: collision → 409, nothing written.
  - `PUT /accounts/{id}`: change to cliente/valor/vencimento colliding
    with another account → 409.
  - Import confirmation: in-file duplicate blocks the whole file;
    collision with an existing account → 409, nothing written.
  - Legacy CSV upload (`POST /upload/`): a row colliding with an
    **already committed** account is **skipped** and counted in
    `duplicados` (not 409). Operators must read that counter.
    Open finding F1 (static reading, not reproduced): two identical rows
    **inside the same legacy file** are not compared with each other
    (the production session has `autoflush=False`, so rows added earlier
    in the file are not visible to the duplicate query). Both rows would
    be inserted.
    Prepared fix (TASK045, **PREPARED_NOT_EXECUTED**, regression tests
    written but **not run**): accepted rows reserve their v1 identity in
    memory; a later row of the same file with the same normalized identity
    is counted in `duplicados` and not inserted. Rows that fail
    validation or are skipped reserve nothing. Until that change has been
    tested and merged, the rule is **not claimed** for in-file legacy
    duplicates.
    Version scope: F1 applies to the **v1/unreferenced** regime. The
    legacy upload creates only unreferenced titles, so F1 stays valid
    under the target design and needs no adaptation now. Referenced
    intake goes only through import v2.
- Until the stable reference is implemented, two legitimate titles that
  coincide externally (same client, value and due date, for example
  installments) are **not representable**. No pilot with such cases can
  start before the implementation is accepted.
- No bypass: names, values or dates must not be altered to get past the
  rule.

## 3. D2 — Human separation of duties

Status: PO recommendation within the user-approved MVP scope. No
standalone named acceptance of this section exists.

- The approver must be a **named human distinct** from the requester and
  from the executor. Requester and executor may be the same person
  (GAM UP-1).
- The code enforces distinct **user IDs** only. Distinct **humans** are
  not technically enforced; they are an operational rule of this policy,
  and the pilot entry record must name the human behind each user ID.
- No shared credentials. One account per human; no account held by two
  people.
- Credential reset: **no approved reset workflow exists**
  (`CUSTOMER_DEPLOYMENT_RUNBOOK.md`, "Limitação de password recovery";
  `reset_test_password.py` is untracked and not approved). A reset may
  only happen through an admin procedure that is written and recorded
  before use. No reset may be used to act as, or obtain the credentials
  of, another role holder (in particular, to approve).

## 4. D3 — Data-entry writes outside the governed action set

`POST`/`PUT`/`DELETE /accounts`, import confirmation and the legacy CSV
upload are **RBAC-gated data-entry writes outside the GAM V1 action set**.
They cannot change `status`: `AccountCreate`/`AccountUpdate` have no
`status` field, and the legacy upload forces `aberto`.

Being outside GAM V1 does **not** mean these writes need no audit. Manual
entry and CSV entry are legitimate product use (including a hybrid of
both), and they change financial fields that the governed actions later
act on.

Actual audit coverage at tree `2dc993cc` (static reading; **not all
manual operations are audited**):

| Write | Durable audit record today |
|---|---|
| Import confirmation | Receipt + `import_confirmation_audit_events` (actor, key, content hash, account refs) |
| `PUT` changing `vencimento` | `account_vencimento_changes` (actor, previous/new) |
| `PUT` changing cliente/valor/email/whatsapp | **None** |
| `POST /accounts` | **None** durable; a best-effort advisory proposal, written after the account commit, whose failure is swallowed |
| `DELETE /accounts/{id}` | **None** for the delete itself. Merged guard (PR #11): under the identity lock and a `FOR UPDATE` lock on the account row, an account referenced by any `account_events`, `account_vencimento_changes`, `approval_requests.target_account_id`, `policy_authority_consumptions.target_account_id` or `business_effect_verifications.target_account_id` row gets 409 and nothing is deleted. Accounts without such history are still hard-deleted (204), with no record. Existing `RESTRICT` references (memory item, NBA snapshot, work item) still block at the DB as before |
| Legacy CSV upload | **None** (no actor recorded; only the HTTP response summary) |

The PO accepted the TASK044 contract (option A1) with explicit
amendments (`PO-DECISION-044-20261031.json`); hybrid manual/CSV entry
stays enabled.

Prepared treatment (TASK045, **PREPARED_NOT_EXECUTED**: not merged, tests
not run, migration `c3a8f1e6d204` not applied anywhere): one isolated,
append-only table `account_data_entry_audit_events` records create,
update and delete of accounts by the manual API, and create by the legacy
CSV upload. Design:

- Written in the **same database transaction** as the business write. If
  the audit insert fails, the business write is rolled back.
- The actor is the **human user of the authenticated session**
  (`actor_type='user'`, user ID, session ID). There is no system or
  fallback actor; a direct service call without an actor fails before
  any database access.
- Financial fields (`valor`, `vencimento`) are stored before/after.
  Name, e-mail and WhatsApp are stored only as domain-separated SHA-256
  digests of the persisted value.
- Legacy rows also record the SHA-256 of the uploaded file and the row
  number.
- A PUT that changes nothing, a rejected write (403/404/409/503) and a
  skipped or invalid legacy row produce **no** audit row.
- The table has no foreign key, so deleting an account does not erase its
  audit rows. The table is **not** part of the DELETE history guard, so
  audited accounts stay deletable.
- Import confirmation keeps its own audit and is not changed.

The stable-reference implementation (§2) will extend this table in a
**separate, later** migration (reference fields in `changed_fields`).
`c3a8f1e6d204` stays as prepared.

Until that change has been tested and merged, the gaps in the table above
remain open.

Non-guarantees of the prepared treatment (they stay true after merge):

- Digests are **unkeyed**. They are **not anonymous and not encrypted**;
  e-mail addresses and phone numbers can be recovered from them by
  dictionary attack. They are pseudonymous personal data.
- Append-only is enforced by a row trigger against UPDATE and DELETE.
  `TRUNCATE` is not blocked (test cleanup needs it), and the table owner
  or a superuser can disable the trigger.
- Reads are not audited; there is no API endpoint or UI for the table.
- `request_id` can be supplied by the client; it is correlation only.
- Writers outside the application (direct SQL, `psql`,
  `migrate_sqlite_to_postgres.py`) are not audited.
- Accounts created before the migration have no create row; history is
  not backdated or synthesized.

Retention and access (PO amendment): only synthetic data may be written
to this table now. There is no automatic purge in this lot. Before the
area5 pilot is activated with real data, a **named** procedure for
retention, access and backup of these rows must be recorded. No legal
retention period is chosen here, and indefinite retention of real data
is not accepted by this document.

Schema recovery evidence (PO amendment): the area1 current-schema
recovery evidence covers head `b428d1a6c903` (37 tables) and is
preserved as evidence for that head. The new head adds one table and one
trigger. The earlier area1 checks are not repeated. Every later schema
head (title reference, reconciliation tables) needs its own
schema-specific delta only.

Targeted recovery gate for `c3a8f1e6d204` (TASK047, **proposed, not
run, no PASS**): a step in the existing backend CI, after the normal
tests and before E2E. On a fresh GitHub-hosted service database
`auneron_test` it would:

- seed one synthetic audit row;
- dump the database with the native `pg_dump` and restore it into a new
  database created by the same run;
- check there: 38 tables, the table's 24 columns, its 22 named CHECKs,
  the index, the function and the enabled trigger, the same row hash,
  and UPDATE/DELETE still rejected;
- drop only the database it created and remove only its own row.

The gate's GitHub context checks are not proof of human approval. The
gate counts as passed only after a PO-approved run reports PASS with
its cleanup verified. A failure, timeout or uncertain cleanup is never a
pass.

Residual (PO-accepted at this scope, not fixed): approval-request
creation checks that the target account exists without locking it. A
request created concurrently with a DELETE of that account can remain
without its target. From reading the code, executing such a request fails
closed at authorization (scope not found) and has no effect. The PO
accepted this only as an explicit missing-target, fail-closed residual at
this scope. It is **not** claimed that such orphaned requests cannot
exist.

## 5. D4 — Autonomy level for the MVP

Status: PO recommendation within the user-approved MVP scope. No
standalone named acceptance of this section exists.

- MVP = **L2, human-supervised**, for the two actions above only.
- The L3 policy worker for `account.mark_overdue` stays **not activated**:
  no fresh grant is issued by this policy or by any MVP step.
- This does not remove or deny the historical L3 capability recorded
  elsewhere; it only keeps it inactive.
- Assisted reconciliation does not change this: no automatic
  reconciliation and no LLM decision. The LLM stays in synthetic shadow
  with no financial writes and no real bank data.

## 6. D5 — Meaning of `mark_paid`, declared vs reconciled

Status: PO recommendation within the user-approved MVP scope. No
standalone named acceptance of this section exists.

- `account.mark_paid` (current version, input `account_id` +
  `expected_status`) is a **human assertion** that payment happened
  ("declared payment"). It is not bank proof. `payment_observed` remains
  unproven (GAM UP-3).
- The current mark-paid approval does **not** cover any bank movement.
- Assisted reconciliation (area4, **not implemented**) will use a
  separate, explicitly versioned contract (`mark_paid.reconciled.v1`,
  CONTRACT-046 §C). Its approval binds the title and the bank movement
  through snapshot digests. The payment effect, the reconciliation link
  and the audit are written in **one** database transaction, and neither
  the movement nor the title can be used twice. Calling the current
  service and then inserting a link is not atomic and is not allowed.
- A statement CSV uploaded by a user is **manual documentary evidence**.
  It is not authenticated bank confirmation and not proof of available
  balance. Reports must distinguish "declared" from "reconciled to an
  imported statement".
- Partial, grouped, fee, FX and refund cases are exceptions. They never
  trigger an automatic mark-paid.

## 7. Replay of a consumed approval (both human corridors, merged)

For both `account.mark_paid` (PR #11) and the human
`account.mark_overdue` corridor (PR #12), replaying an execute call for
an already consumed approval returns the stored receipt
(`duplicate=true`, no write) only if all of these hold:

- the caller is the **original executor** (`authority_user_id` of the
  consumption);
- that user is still active and still passes the **current**
  authorization check (including mutating authority and separation from
  the approver);
- the **input** matches the approved input digest. For mark-paid this
  means the same `account_id` and `expected_status`. For mark-overdue the
  account and due date are bound by the canonical work key in the URL,
  and the digest is revalidated;
- the **receipt chain** is consistent (decision, input digest,
  fingerprint, runtime key, invocation, output, and for mark-overdue the
  receipt work event), and the account is in the resulting status.

The approver, other users, and changed input are rejected (403/409)
without the receipt. Expiry of the approval after consumption does not
invalidate the receipt. The L3 policy path is not covered by this
contract; it has no HTTP route and is not activated (D4).

## 8. Authority provenance

- An agent-generated JSON file or receipt is transport, **never human
  consent**.
- Any mutable DB, migration, deploy, grant or credential action needs
  verifiable human approval given **before** execution and bound to the
  operation, target and scope.
- Including a feature in the MVP scope is **not** approval of
  publication, migration, database operations, deploy or activation.
- Product developer and reviewer are separate roles. Any exception is
  recorded explicitly; it is never inferred or granted retroactively.

## 9. Open items (not closed by this document)

- Named human acceptance of D1-D5 (dated record): **pending**. D1 must
  be rewritten around the stable reference before it can be accepted.
  D2, D4 and D5 are recommended MVP scope from the PO scope decision,
  not accepted standalone.
- D3 audit gaps: TASK045 prepared; its authorized test run on a
  disposable `auneron_test`, the migration application there, review and
  merge are **pending**.
- Legacy CSV in-file duplicate finding F1 (§2): fix prepared in TASK045,
  **not tested**.
- Stable title reference (§2): design in CONTRACT-046 §A, implementation
  **pending** (next area2 bundle, after TASK045).
- Targeted recovery delta for the new audit table and trigger (§4):
  gate prepared in TASK047, **proposed, not run**. Its first execution
  needs specific approval: a fresh GitHub run that applies migration
  `c3a8f1e6d204` to the CI `auneron_test`, seeds one row, dumps and
  restores natively, creates and drops one owned database, and
  TRUNCATEs the audit table under its guard.
- Real-data retention, access and backup procedure for the audit table
  (§4): named decision required before area5 pilot activation.
- If the first pilot's bank export has no stable movement identifier, an
  explicit format-adaptation decision is required before the pilot.
- H1 (28/10 mutable execution on `auneron_test` without prior verifiable
  consent): **open**. The user's factual acknowledgement is still
  pending; the scope expansion is not that acknowledgement. Needs the
  user's own dated statement and a PO closure record.
- H2 (PO-authored schema/migration code `b428d1a6c903`): the PO recorded
  the authorship exception and the independent review in
  `PO-H2-AUTHORSHIP-EXCEPTION-20261031.md`. That record is not an
  authorization of any execution.
- Raw historical records are not backdated or rewritten to close any of
  these items.
