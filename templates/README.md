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
or [shared learning](conventions/learning.md). Use subcommand `--help` for arguments.
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

- Default: CLAUDE_01 is the admin interface, executor and integrator; CODEX_EXPERT
  proactively analyzes and reviews. Expert edits require an explicitly assigned implementation task.
- Full: CODEX_01 is the default executor/integrator, CLAUDE_EXPERT studies model/prompt/schema/evidence,
  and CODEX_EXPERT studies runtime/SDK/lifecycle/concurrency. CLAUDE_01 coordinates and may implement
  independently assigned work. Lenses are not exclusive ownership.

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

Only the admin's main session initializes, starts or changes room mode. A mode has two or four
persistent members; do not silently expand the team. Native subagents require existing task/host
authorization and remain their parent's responsibility.
