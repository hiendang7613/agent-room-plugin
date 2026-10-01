# Room CLI for members

Run `agent-room --help` and subcommand `--help`. All results are JSON: `ok`, then `data` or `error`.
An error with `committed: true` means the state mutation succeeded but its Markdown projection failed;
inspect the returned data instead of repeating the mutation. Do not use generic shell eval or concatenate
admin/peer text into shell commands. Pass JSON/text via a quoted heredoc or an existing file.

## Receive and account for work

The main receives a P-... receipt from UserPromptSubmit. Read `intake list` for missing receipts.
If a hook failed, main may recover the original human text with `intake recover --source-ref 'original message reference' --body-file original.txt`.
This records manual recovery explicitly. Never recover peer text as human input. A manually recovered
receipt cannot answer a native permission request; obtain a fresh human receipt or use the native UI.
For each intent, create/update a task/note or answer it, then use:

    agent-room intake account P-ID --disposition 'Classified the implementation and answered the status question' --refs T-ID

`task create --input -` accepts an object with title, request, acceptance, next, owner, source (P-ID),
authority (`analysis` or `implementation`), scope (relative file/directory paths), and dependencies (task IDs).
Default expert authority is analysis. Only main assigns tasks/authority. Creation wakes the assigned member;
read the task and its dependencies before starting. A dependency need not be approved again.

    agent-room task list
    agent-room task list --all
    agent-room task show T-ID
    agent-room task claim T-ID --expected-version 1

Claim returns a token and current task version. Keep it for `task release T-ID --token TOKEN`.
`task update T-ID --expected-version N --input -` accepts state, checkpoint, next, evidence,
snapshot and blocked_reason. Only main may also change owner/scope/authority/request/acceptance with
a fresh source receipt. Read before updating after a version conflict. Do not blindly retry an old patch.
States: ready, running, blocked, review, done, cancelled. Evidence is required for done.
Use `snapshot path/to/file ...` to capture source hashes; pass its data object as snapshot with a review.

Schema 2: set review_policy and reviewer on tasks requiring peer review, then use `task submit`,
`review record`, `task checkpoint` and `task context`. See [evidence guide](evidence.md) for JSON contracts.
The legacy optional snapshot alone does not satisfy peer_required completion.

## Questions and decisions

`note add --input -`: kind (question/proposal/decision), body, tasks (affected IDs), optional condition.
Decision or approved records require main and an original source receipt. Questions/proposals start open.
`note resolve ID --expected-version N --input -`: state, answer, source, optional condition_evidence or
superseded_by. Resolve each exact displayed proposal ID; do not interpret unrelated historical approval
as a response to a new proposal. Allowed states are visible in the operation's errors and the README.
Approved proposals/decisions associated with tasks gate starting/completing that task when conditional.
Use separate analysis tasks if a pending implementation proposal still needs research.

## Peer messages and native prompts

    agent-room send --to CODEX_EXPERT --task T-ID --body-file finding.txt
    agent-room send --to CLAUDE_01 --body 'Concrete finding and next action'
    agent-room inbox --after 0 --limit 50
    agent-room ack M-ID --evidence 'Reviewed current task and saved findings in reviews/topic.md'

Read next_after until null. Inbox exposes stale context; acknowledge only after reconciling/processing.
For short messages use `--body` with shell-quoted text. For multiline messages use an existing project file
or pipe text through stdin. A shell heredoc may need a temporary file that the native sandbox disallows;
use the inline argument or a pipe in that case instead of requesting broader permissions.
Sending stores the message. Status progresses through queued, dispatching, submitted (Claude) or accepted
(Codex), then processed. Unknown/failed dispatches require explicit main reconciliation before
`retry-message ID --source P-ID --reconciled '...'`. Native held/refused messages may remain submitted
without a processing receipt; investigate native inbound settings, never loosen them automatically.

`approval list` shows native requests. Main asks the admin about the exact action, then uses
`approval respond A-ID --source P-ID --decision accept|decline|cancel` for supported Codex requests.
The P-ID must be the explicit human answer to this request. No peer may supply approval. Unsupported
request kinds remain pending; use their native UI or cancel the native turn/room. Never fabricate an answer.

Use `history --kind messages|events|prompts --after N --limit 50` for complete paginated history.
Task/decision Markdown views are generated; use these operations instead of editing them.
