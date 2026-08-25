# Continuity and Divergence Runbook

**Status:** mandatory operational guardrail from Commit 24D onward.

## Purpose

This runbook defines how to investigate any divergence between handoff/master
documentation, terminal state, Git, Alembic and stage manifests. Diagnosis is
read-only until evidence is captured. Never choose one source automatically.

## Mandatory diagnostic order

Run from `C:\Users\Tomaz\Documents\GitHub\auneron\backend` unless a
stage-specific script says otherwise.

```powershell
git symbolic-ref --short HEAD
git rev-parse HEAD
git fetch origin main
git rev-parse origin/main
git status --porcelain=v1 --untracked-files=all
git diff --cached --name-only
git rev-parse --absolute-git-dir
python -m alembic heads
```

Then validate the latest approved Gate audit and post-APPLY manifest/hash set
using the stage-specific Source Capture/Gate. The stage script is the preferred
mechanism because it knows the exact expected path set and hashes.

## Git operation markers

After resolving the absolute Git directory, inspect for active operation markers:
`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `BISECT_LOG`, `rebase-apply` and
`rebase-merge`. Any marker is a STOP condition.

## Source-of-truth reconciliation

1. Preserve the terminal output and current `git status`.
2. Verify local HEAD and `origin/main`.
3. Verify staging is empty when the checkpoint requires it.
4. Verify the exact changed-file set.
5. Verify Alembic source head.
6. Verify the latest approved audit markers and post manifest hashes.
7. Only after all evidence is understood decide whether documentation is stale,
   an artifact is wrong, or the repository actually changed.

## STOP conditions

Stop before any write if branch/baseline, origin, staging, changed-file set,
manifest hashes, Alembic head, DB safety or active Git operation differs from the
expected checkpoint.

## Prohibited during diagnosis

Do not run `git reset`, `git checkout`, `git restore`, `git clean`, rebase,
force-push or any script that writes the repository before the divergence has
been explained and evidence preserved.

## Automation invariant

Every new Source Capture/APPLY/Gate must be self-validating and fail-closed. An
APPLY must validate the previous approved checkpoint and create recovery before
its first repository write. User-facing execution commands must verify the
script SHA-256.
