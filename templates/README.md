# Agent Room

This project uses native Claude Code and Codex sessions and one shared room ledger.
At startup/resume, read this page and the [working agreement](rules/working_agreement.md),
then refresh `agent-room --json status --compact`. Status contains current tasks, decisions,
waiting work and `pending_inboxes`. If your member has an entry in `pending_inboxes.by_member`,
run `pending_inboxes.read_command` and follow `next_after` until null. If absent, skip the inbox
sweep for this snapshot; process later native messages normally. Generated
[task](tasks/active.md) and [decision](state/current_decisions.md) files are optional views.
Preserve any additional reading required by this project's own instructions.

Open guides when useful for the work at hand: [CLI operations](conventions/cli.md),
[review/checkpoints](conventions/evidence.md), [collaboration](conventions/collaboration.md),
[shared learning](conventions/learning.md), or [admin-facing response style](conventions/response-style.md).
Use subcommand `--help` for arguments.
After a plugin upgrade, `agent-room guide` lists current shipped references by topic; for example,
`agent-room guide collaboration`. Existing room guides stay preserved and may contain custom rules.
The reference reports its CLI's plugin version; project-specific instructions still apply.

Members are encouraged to ask, share ideas, challenge, remind and help each other directly.
Ordinary discussion needs no task or prescribed rounds. Use formal tasks to track committed work;
keep file ownership, admin authority and native permissions clear when acting on an idea.

## Roles

Admin describes goals in ordinary language; main operates the room tools and reports useful outcomes.
See [collaboration](conventions/collaboration.md) for the interface and peer freedom. After upgrading,
`agent-room guide collaboration` serves the current reference without replacing custom project rules.

- Every new room has four persistent, addressable members in the default mode: CLAUDE_01 is the admin
  gateway, CODEX_01 and the two experts receive room messages and may contribute within existing
  authority. `full` remains a compatibility alias for this same roster.
- A logical room message queues deliveries for all other members. The direct addressee owns its
  request/task; other copies are FYI, but still wake their recipients. Paused/stopped members keep
  messages queued until delivery is available.
- The four model/effort defaults are currently recorded as roster metadata but are not passed to
  native launch; agents inherit each host's active model/effort configuration.

Use `agent-room --json status --compact` for current mode, native IDs, errors and waiting work.
For a pending sweep, follow `next_after` through every page; `read_command` starts fresh sweeps
at `--after 0`. A pagination cursor is not a saved read position.
Only a processing ACK removes a message from this view. Failed/unknown deliveries still need
reconciliation; a pending message does not authorize replay. Use `inbox` without the flag for history.
For long or already-delivered text, `inbox --pending --compact` offers previews and full read commands;
see the [CLI guide](conventions/cli.md). Process full content before acting or ACK.
Use `agent-room --json history` for the durable room conversation and `history --kind events`
for native final responses and state events. Native private conversation histories stay native.

The SQLite database in .runtime is authoritative for tasks/decisions/messages. Generated active
task and decision Markdown are views; do not edit them. Long reviews and evidence can be ordinary
files under reviews/. Checkpoints and metadata are local/private by default, not automatically published.

Only the admin's main session initializes or starts the room. Default and `full` use the same four
members, so selecting the alias does not change membership or require a task handoff. Native
subagents require existing task/host authorization and remain their parent's responsibility.
