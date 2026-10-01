# Source-bound review and recovery — schema 2

Use `agent-room task context T-ID` before resuming or acting on a late message.
Read every full-record pointer when the context is truncated. Reconcile changed owner,
decision, source, native session/generation and unknown effects before repeating actions.
Native history stays native; this pack supplies current project obligations.

Main selects `review_policy: "none"` or `"peer_required"` at task creation. For peer review,
set `reviewer` to an active member different from owner. Use it for consequential code
changes, not every status question. Changing policy/reviewer requires a main/admin receipt.
It grants no new implementation/provider/commit authority to either participant.

## Author submits

Finish scoped work and checks, then:

```text
agent-room task submit T-ID --expected-version N --input submission.json
```

`submission.json`: `{"paths":["src/file.py","reviews/checks.md"],"summary":"What changed","evidence":["Exact check and result"]}`.
Use actual individual files; include deleted paths to record absence. At least one source/report file
must exist. The store computes hashes; the author cannot supply or replace them. This freezes the
declared review set, enters review and releases the author's writer claim. It does not prove that
the set includes every relevant file. Main/reviewer must check scope completeness and acceptance.

## Assigned peer reviews

Read `agent-room submission show S-ID`, current task context and listed source files.
Run the authorized checks needed for acceptance. Record:

```text
agent-room review record S-ID --input review.json
```

`review.json`: `source_digest` (exact submitted digest), `verdict` (`approve`, `changes_requested`,
`blocked`), `summary`, `findings` and nonempty `evidence`. Findings contain `summary`, `severity`
(`high`, `medium`, `low`), optional submitted `path` and positive `line`.
Approve only with no unresolved findings. Missing/changed source requires reconciliation and a new
submission. Receipts are immutable; an identical retry returns the existing receipt.

For corrections, follow previous_submission and review_receipts from `submission show` to read earlier
findings. The author moves the task back to ready, claims its scope, fixes and submits again.
Changes to submitted evidence or dependency revisions also invalidate review. Main marks a peer-required task done only after the matching receipt is approved and
dependencies/decisions/evidence still satisfy the task. A reviewer receipt cannot authorize shipping.

## Checkpoint and inspect execution

```text
agent-room task checkpoint T-ID --expected-version N --input checkpoint.json
agent-room checkpoint show C-ID
agent-room attempt list --task T-ID --after 0 --limit 50
agent-room attempt show E-ID
```

Checkpoint input: `summary`, `last_safe_action`, `next`, `paths` (individual files) and
`unknown_effects` (array, empty when none). The store records sequence, owner, source hashes,
decisions, native ID and generation. A checkpoint never releases a live writer's claim.
Keep legacy text checkpoints readable; use the structured command for new recovery records.

Attempts describe dispatch/native evidence, not semantic success. A completed Codex turn can have
empty/unobserved output; an ACK only records processing. Claude inbox provides no turn/output receipt,
so those fields stay unknown and the peer records findings via task/review/checkpoint operations.
Output event_seq points into `history --kind events --after (event_seq-1) --limit 1`.
One native turn can include multiple steered messages; shared output is not proof of a separate
contribution for every task. Model/usage/cost is not inferred. Unknown effects are never auto-retried.
