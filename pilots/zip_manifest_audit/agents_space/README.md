# Agent Room

This project uses native Claude Code and Codex sessions and one shared room ledger.
Read [working agreement](rules/working_agreement.md), [active tasks](tasks/active.md),
[decisions](state/current_decisions.md), and [CLI guide](conventions/cli.md) at startup/resume.
For source-bound review/checkpoints, read [evidence guide](conventions/evidence.md).

## Roles

- Default: CLAUDE_01 is the admin interface, executor and integrator; CODEX_EXPERT
  proactively analyzes and reviews. Expert edits require an explicitly assigned implementation task.
- Full: CODEX_01 is the default executor/integrator, CLAUDE_EXPERT studies model/prompt/schema/evidence,
  and CODEX_EXPERT studies runtime/SDK/lifecycle/concurrency. CLAUDE_01 coordinates and may implement
  independently assigned work. Lenses are not exclusive ownership.

Use `agent-room --json status` for current mode, native IDs, errors and waiting work.
Use `agent-room --json inbox` for your messages; process all pages when next_after is present.
Use `agent-room --json history` for the durable room conversation and `history --kind events`
for native final responses and state events. Native private conversation histories stay native.

The SQLite database in .runtime is authoritative for tasks/decisions/messages. Generated active
task and decision Markdown are views; do not edit them. Long reviews and evidence can be ordinary
files under reviews/. Checkpoints and metadata are local/private by default, not automatically published.

Only the admin's main session initializes, starts or changes room mode. A mode has two or four
persistent members; do not silently expand the team. Native subagents require existing task/host
authorization and remain their parent's responsibility.
