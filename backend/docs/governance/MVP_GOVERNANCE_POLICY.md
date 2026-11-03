# MVP Governance Policy

## Final PO acceptance record — 2026-11-03

Status: ACCEPTED_FOR_INTEGRATION; area 2 closes administratively only when
this record is merged after the current-head CI succeeds.
This record supersedes the pending-acceptance/H1 status statements in the
dated preparation sections below; those sections remain historical evidence.

Daniel Tomaz authorized the consolidated policy with D4/D5 amendments,
explicitly accepted those adjustments, and then answered "confirmo" to:
"voce confirma que tomou ciencia do relato de execucao no banco de testes
em 28/10 sem consentimento previo verificavel?"
H1 acknowledgement is therefore recorded on 2026-11-03. The PO closes its
acknowledgement/recording gate, preserving the original incident. This is
not retroactive authorization, exoneration or a claim of new remediation.

PO review: integrated audit, identity and v2 evidence is verified.
D1-D3 safeguards and the amended D4/D5 product direction are accepted.
CI 41645049326 passed for acceptance commit 5a3625e; this final record's
commit must also pass the applicable CI before integration.
Runtime L3 activation and automated payment reconciliation remain tracked
implementation/activation deliverables with the gates below. Neither is
claimed live by closing area 2. No deploy or live database/grant operation
is authorized or performed by this documentary closure.

## Current governance review — 2026-11-03

Status: TECHNICAL_EVIDENCE_VERIFIED / CONDITIONAL_ACCEPTANCE_WITH_D4_D5_AMENDMENTS.
Area 2 is not yet administratively closed.

This section supersedes the implementation-status claims in the historical
proposal below. The proposal is preserved as history, not as the current
delivery register. Neither this review nor a CI result supplies human consent.

### Verified baseline and evidence

Reviewed main: `8f4daa10b6eda7c4bde77fe5464220492c729c96`.
Schema migration head exercised in CI: `e9a4c7d2b135`.

| Delivery | Integrated evidence |
|---|---|
| Data-entry audit and legacy duplicate fix | PR #13; merge `3714651335719d69766a2c2c3982397e81f7ba21` |
| Stable title reference TI1 | PR #14; merge `ab0a5575ec457a536b185e1fc48354d6fec22887` |
| Import identity v2 TI2 | PR #15; merge `8f4daa10b6eda7c4bde77fe5464220492c729c96` |

PR #15 CI: [41558329527](https://github.com/danit0m/auneron/actions/runs/41558329527).
Post-merge CI: [41564935880](https://github.com/danit0m/auneron/actions/runs/41564935880).
Both completed successfully; the latter belongs to the merge commit above.
The PR run recorded 3,859 general tests, 20 existing PostgreSQL import tests
and six dedicated native v2 tests passing. Recovery, 48 CHECK-equivalence
probes, authenticated E2E and final database cleanup passed.
These are CI/disposable-environment claims, not production deployment claims.

### D1-D5 — conditional acceptance and amended product direction

| Decision | Rule |
|---|---|
| D1 — identity | Internal identity stays accounts.id. Origin + reference is unique in the immutable side table. Different references permit legitimate equal financial tuples. An unreferenced tuple collision blocks for review; no invented references or altered financial fields to bypass it. |
| D2 — human separation | The human approval corridor retains independent approval. The L3 target uses explicit predelegated policy authority and an honest system actor; it does not fabricate human approval for each transaction. Grant approval/revocation and access rules must be defined for each activated corridor. No shared credentials or impersonation/reset bypass. |
| D3 — data entry | Manual and legacy CSV writes remain RBAC-gated outside GAM V1, with transactional append-only data-entry audit. Import keeps its receipt/audit contract. This does not authorize financial status changes through data entry. |
| D4 — autonomy | Daniel's approved product direction is governed L3 in the MVP: eligible routine actions execute under preapproved policy, without per-item human approval; humans handle exceptions and supervise policy. This supersedes an L2-only MVP ceiling. The existing overdue pilot is evidence only for its tested corridor, not generalized activation. LLM output alone grants no authority. |
| D5 — payment | Daniel's approved product direction is automated reconciliation and payment posting for eligible, unambiguous cases, with humans handling exceptions. Current mark_paid remains a declaration until a distinct evidence-bound reconciliation corridor is implemented and validated. Area 4 must support this automated normal path; assisted-only processing is no longer the target. |

TI1 uses the existing side table, a shared identity lock, database uniqueness,
a RESTRICT FK and append-only protection. Manual assignment and its audit
commit atomically. Assigned references cannot be edited or deleted through
the product.

TI2 explicitly selects identity_version=2 and origem, requires referencia,
and includes origin/reference in its semantic digest. Equal existing
reference/payload is matched without insertion; changed payload is rejected.
New references colliding with unreferenced accounts require TI1 mapping and
a new preview. File errors or transactional failures do not partially import.
Same actor/key/digest replays the stored receipt without new writes.
All-matched files return nothing_to_import without creating a receipt.
V1 digest/receipt replay remains compatible. V1 is not retired by this record.

The preview is database-free: existing-title classification occurs at confirm.
The UI does not yet submit v2. Confirmation remains under the existing
test-only guard. These are accepted delivery boundaries only if the named
acceptance below is recorded; no production import activation is implied.

### Residuals carried to their owning gates

- Area 3: synthetic LLM shadow only; model output never creates authority.
- Area 4: manual-create idempotency/create-with-reference, v2 UI as needed by
  the journey, and automated evidence-bound reconciliation with exception handling. No claim that these are delivered.
- Area 5: named retention/access/backup/erasure procedures for references and
  audit data before real data; references currently cannot be erased through
  the product. Unkeyed personal-data digests are pseudonymous, not anonymous.
  A written credential-reset procedure and person-to-user mapping are needed
  before the applicable pilot operations.
- Single company per installation and single application replica.
  Multi-instance guarantees and UP-2 authority convergence remain deferred.
- Audit row triggers do not stop TRUNCATE or privileged owner/superuser
  bypass, direct SQL is outside application audit, reads are not audited,
  and pre-migration history is not synthesized.
- The existing concurrent approval-creation/account-delete missing-target
  residual remains explicit and fail-closed, not repaired by this delivery.
- H2 remains the previously documented authorship exception; this review is
  PO-authored documentation only, not a new schema or runtime implementation.

### Daniel Tomaz's conditional authorization — 2026-11-03

Daniel authorized governance closure with explicit reservations: D4 should
include active L3 to reduce human work, and D5 should automate payment
verification rather than require routine manual statement checking.
This is conditional acceptance with changed scope, not acceptance of the
previous L2-only/assisted-only wording. D1-D3 retain their safeguards; D2's
per-action human corridor is distinguished from predelegated L3 authority.

The PO records the requested activation outcome. No grant was created and
no runtime activation occurred during this documentary amendment.
The target environment, eligible action set, limits and evidence sources
have not yet been bound to an operational activation plan. The approval is
not expanded into an unspecified production or deployment-wide grant.

### Explicit acceptance of amended D4/D5 — 2026-11-03

Daniel Tomaz confirmed: "ok... autorizado ajustes em D4 e D5."
This explicitly accepts the amended D4/D5 product direction recorded above:
governed L3 for eligible routine operations and automated evidence-bound
reconciliation/payment posting, with human exception handling.
The prior D4/D5 wording is superseded; no further acceptance of these same
documented amendments is pending. Implementation and operational activation
gates below remain applicable; no specific live grant or environment is
selected by this statement. H1 acknowledgement is not inferred.

### D4 implementation and activation gates

Existing evidence:
[POLICY_AUTONOMOUS_WORKER_L3_PILOT_EVIDENCE.md](../operations/POLICY_AUTONOMOUS_WORKER_L3_PILOT_EVIDENCE.md)
proves controlled account.mark_overdue autonomy for one company, using a
predelegated grant, denial without authority and restart without duplicate
effect. The pilot grant was revoked; this is historical evidence, not a
current live-grant assertion. Payment autonomy and multiple simultaneous
episodes were explicitly not proven. Persistent effect verification in that
pilot was manual after the autonomous window.

Prepare the next activation contract against the current code: exact
environment/company, permitted actions, eligibility, policy version, grant
owner, revocation/stop behavior, supported workload and observable outcome
verification. Prove denial, concurrency, replay, restart and recovery for
the intended workload before activating that scope. Do not weaken the
existing human corridor or infer that an overdue grant covers payment.
Document available grant granularity; if it cannot enforce required limits,
implement the missing boundary before activation.

L3 means executing approved policy automatically, not unconstrained model
decisions. LLM shadow validation may remain a development stage, but must
not be confused with delivering the requested operational L3 outcome.

### D5 automated-reconciliation contract to implement in area 4

The normal path must acquire financial movement evidence, deterministically
match it to a stable title and execute an eligible payment posting under
predelegated authority without requiring a human to inspect each statement
or approve every matched item.

Distinguish acquisition from matching/posting: an uploaded CSV can support
automatic processing after upload but is not unattended bank acquisition.
Select the source and integration contract separately; bank/API/ERP access
and credentials are not assumed available. An imported file remains
documentary evidence, not authenticated bank confirmation or balance proof.

Proposed initial eligibility: a uniquely identified title and movement,
exact amount/currency, valid account/company and settlement state, permitted
policy scope, and no prior consumption or conflicting evidence.
A confidence score, client name, amount/date coincidence or LLM assertion
alone is insufficient authority/evidence. These rules are a design proposal
for the new corridor, not claims about current runtime.

Payment status, movement consumption/link, authority consumption, audit and
receipt must commit atomically. Enforce one use of each title/movement with
database constraints and locking. Replay, parallel writers, rollback,
restart and recovery need native PostgreSQL evidence. Preserve the existing
declared-payment corridor and distinguish evidence source in reports.

Ambiguous, partial, grouped, fee, FX, reversal/refund or inconsistent cases
go to an exception queue until their own rules are implemented and proven.
Failure to fetch or validate evidence leaves financial state unchanged.

### Closure status and H1

Area 2's integrated audit, title identity and import-v2 technical evidence
remains verified. The amended governance contract is not yet administratively
closed. Do not claim L3 or automatic reconciliation is delivered by this
document. Track D4 activation and D5 implementation explicitly in the next
execution contracts, rather than silently carrying an L2-only product.

H1 remains OPEN independently: the historical proposal records a mutable
execution on auneron_test on 2026-10-28 without prior verifiable consent.
Daniel's conditional authorization addressed D4/D5 and is not recorded as
factual acknowledgement of H1. Do not grant retroactive authorization,
rewrite the historical incident or infer its acknowledgement. Technical
planning and reversible corrections can continue while this record remains
open; no repeated approval is needed for that authorized work.

This amendment is PO-authored documentation. No migration, database write,
grant, credential change, deploy or live activation was performed.

---

## Historical proposal — retained unchanged below

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
Amended:     2026-11-01, TASK054 stable title reference TI1
             PREPARED_NOT_APPLIED (candidate files staged outside the
             repository; migration e9a4c7d2b135 not applied; PostgreSQL
             tests not run; recovery-gate extension not run)
Amended:     2026-11-02, TI2 import identity v2 candidate for PO review
             (branch review/area2-import-identity-v2; no DDL; tests
             written, not run; not merged)
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

Prepared, **not applied, not merged and not run**: TASK054 TI1, the first
slice of the stable title reference (§2, "TI1"). The PO scope record
`PO-DECISION-054-20261101.json` sets its scope; it is not human
authorization of any database operation, migration, CI run or
publication (`execution_authorized_by_this_record=false`).

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

**TI1 (TASK054, PREPARED_NOT_APPLIED): manual reference on a legacy
title.** Candidate code only. Migration `e9a4c7d2b135` (parent
`c3a8f1e6d204`) is not applied anywhere; its PostgreSQL tests and the
recovery-gate extension have not run. Nothing below is claimed to work
until then.

- Storage (PO decision D1): a separate insert-only table
  `account_title_references` (one row per account, `UNIQUE(account_id)`,
  unique `(title_source, title_reference)`, FK `accounts.id` `RESTRICT`,
  append-only trigger against UPDATE/DELETE). `accounts`, the v1 mirror,
  the v1 import SQL and receipts, and the TASK045 audit table are not
  changed.
- Only path: `POST /accounts/{id}/title-reference` with `clients.manage`.
  It takes the identity advisory lock, then the account row `FOR UPDATE`.
  The values are trimmed, NFC-normalized and validated (source
  `^[a-z0-9][a-z0-9._-]{0,63}$`; reference 1-128 characters with no
  control or invisible format characters).
- An assigned reference is immutable: a second assign for the same
  account → 409 `title_reference_already_assigned`. A reference owned by
  another account → 409 `reference_exists` with that account ID.
- Tuple rule: if another title with the same normalized client, value
  and due date is **unreferenced**, the assign is blocked (409
  `legacy_duplicate_review`, no override, D6). If every coincident title
  is **referenced**, the assign succeeds with a
  `coincident_referenced_tuple` warning.
- `DELETE /accounts/{id}` of a referenced account → 409 through the
  existing governed-history guard (the side table is added to it), with
  the FK `RESTRICT` as database backstop (D7). Unreferenced accounts stay
  deletable as before.
- Audit (D8): the side-table row and one TASK045 `update` row (field
  names only) are written in one transaction; if either fails, both are
  rolled back.
- `POST`/`PUT /accounts` reject only the `title_source` and
  `title_reference` keys (422). Other unknown keys are still ignored, and
  the v1 tuple 409 text is unchanged. Manual creation stays unreferenced
  and keeps the conservative 409 against any coincident title.
- Not in TI1: import identity v2 (TI2, only after TI1 is reviewed and
  accepted), create-with-reference, override, idempotent manual create
  (area4, D2/D3), UI, bank and LLM work.

Personal data and retention (TI1): a reference can contain personal data
(CPF/CNPJ, names, order numbers). It is stored only in the side table.
It is not written to logs, error bodies or either audit table, and only
the caller who supplied it gets it back in the 201 response. The row is
append-only and the account cannot be deleted while it exists, so a
stored reference **cannot currently be erased** through the product.
Only synthetic data may be written to this table now. Real data needs a
named retention, access and erasure procedure before area5 activation.
Known limit: the existing generic 422 response of `POST`/`PUT /accounts`
echoes the request body back to the same caller; it is not logged or
stored by the application. The new assign route answers with fixed codes
only.

**TI2 (candidate for PO review, not merged, tests not run): import
identity v2.** CONTRACT-046 §A5 on top of TI1 storage. **No DDL**: the TI1
side table already allows `assigned_via = 'import_v2'` with
`import_idempotency_key`/`import_content_sha256`; receipts already allow
`identity_version >= 1`; the import audit `action` column is free text.
Head stays `e9a4c7d2b135`.

- Same routes, explicit version: `POST /imports/preview` and
  `POST /imports/confirm` take query `identity_version` (`1` or `2`;
  absent = 1) and, for v2 only, `origem` (the `title_source` pattern).
  The body is still raw octet-stream, so these are query parameters, not
  form fields. v1 requests, v1 responses, the v1 plan/execution modules,
  the v1 adapter and v1 receipts are unchanged; the v1 adapter still
  never reads or writes the side table.
- v2 requires the canonical column `referencia` (aliases `referencia`,
  `referencia do titulo`, `documento`, `numero do documento`; v2 only).
  The value is trimmed, NFC-normalized and validated with the same
  rules as TI1. A missing or invalid value is a row issue
  (`reference_required` / `invalid_reference`). A repeated reference in
  the file is `duplicate_reference`. Either blocks the whole file.
  Rows with the same client, value and due date but different
  references are a preview warning only (`coincident_tuple_in_file`).
- The v2 digest has its own domain (`identity_version`, `origem`, the
  reference and row warnings in the payload), so it never equals a v1
  digest. The v1 digest bytes are unchanged.
- Confirm v2, all under the shared identity advisory lock and in one
  transaction: a reference that already exists with the same normalized
  client, value and due date → `existing_same` (no insert, never
  updated, audit `title_reference_matched`). The same reference with a
  different payload → 409 `reference_conflict`. A new reference whose
  tuple matches an **unreferenced** account → 409 `legacy_match_review`
  (no override; assign the reference via TI1, then re-preview). Tuple
  matches only referenced accounts → created with warning
  `coincident_referenced_tuple`. All rows `existing_same` → 409
  `nothing_to_import`, no receipt. 409 bodies carry only row numbers and
  account IDs.
- Each created account gets its side-table row (`import_v2`, actor
  user ID, import key and digest) in the same transaction as the
  account, the audit rows and the v2 receipt. The receipt covers created
  accounts only.
- Idempotency: a v2 retry with the same actor, key and digest returns
  the stored receipt with no writes. A v1 receipt under the same key
  (or a v2 receipt under a v1 request) → `idempotency_conflict`.
  Matched rows and warnings are reported only on creation; a replay
  returns the receipt alone.
- Stated limits: the preview stays database-free, so classification
  against existing titles (`existing_same`, conflicts, referenced-tuple
  warnings) appears only at confirm time. The side-table row from import
  has no `auth_session_id`/`request_id` (the confirm route passes only
  the `user:<id>` actor). Still registered only under the test-only
  confirmation guard. The UI does not send v2 yet. Retiring v1 for new
  confirmations remains a PO decision.

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

The stable-reference implementation (§2, TI1, PREPARED_NOT_APPLIED)
does **not** change this table: there is no audit DDL. A manual assign
writes one `update` row in the same transaction as the side-table row,
with `changed_fields = ["title_reference", "title_source"]` and identical
before/after snapshots of the audited account fields. The reference
value itself is never stored here. `c3a8f1e6d204` is unchanged.

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

TI1 extension of the same gate (TASK054, **PREPARED_NOT_APPLIED, not
run**; PO decision D9 rejected a catalog-only check): head `e9a4c7d2b135`,
39 tables. In addition to the audit proof above it would:

- check `account_title_references` strictly on source and target:
  11 columns, 7 named CHECKs (no IN lists, so no rendering allowlist),
  the exact PK/UNIQUE/FK `ON DELETE RESTRICT` definitions, its 3 indexes,
  the function and the enabled trigger. If CI renders any definition
  differently, the run fails and the difference must be reviewed;
- seed exactly **one** synthetic account and **one** reference (run
  marker) into the empty CI `auneron_test` only, and record their IDs,
  full-row fingerprints and the initial zero counts;
- after native restore, require both rows to be byte-identical, the
  reference UPDATE/DELETE rejected and the account DELETE refused by the
  FK;
- clean up in a **separate** step and one transaction: lock `accounts`,
  then the side table; proceed only if both were empty at the start, now
  hold exactly the run's rows, and no other row references the account;
  then TRUNCATE only the side table and DELETE only the owned account ID.
  It never truncates `accounts` or `users`, never deletes an unknown
  account, and never disables a trigger or FK. Any other state is
  reported and blocks PASS.

These extra seed and cleanup steps widen the gate's write surface. A new
human authorization for that exact CI scope is required before the first
run. The 48 CHECK probes and the 180 s owned-target DROP budget are
unchanged.

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
- Stable title reference (§2): TI1 (manual assign) **prepared, not
  applied, not run** (TASK054). PO review, then a separately authorized
  test run, CI recovery-gate run and merge are pending. TI2 (import v2):
  candidate prepared for PO review (§2), offline tests written but
  **not run**, no PostgreSQL test of the v2 path yet (unique-index
  backstop, lock interplay with `POST /accounts/{id}/title-reference`).
- Retention, access and erasure procedure for stored title references
  (§2, TI1): named decision required before real data or area5.
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
