# Agent Room

A native Claude Code + Codex team for this project. Describe the outcome in ordinary language;
CLAUDE_01 operates room coordination and reports useful progress, results and necessary decisions.
Members can ask, brainstorm, challenge, help and share experience directly within existing authority.

**Version 0.3.20, schema 3.** This release flags failed/unknown inbox deliveries,
names the room reply channel, preserves resume guidance and skips duplicate fresh-start guidance.
Reserved peer envelopes are a defensive classification guard, not proof of human authorship.
Offline checks are distinct from native model acceptance and comparative performance;
neither has been established for this package. Internal bookkeeping remains inspectable.

## Install and begin

Requires macOS, Python 3.11+, Claude Code background sessions and Codex app-server. Sign in through
the native CLIs, then extract this archive to a stable directory. In Claude Code:

```text
/plugin marketplace add /absolute/path/to/install/agent-room
/plugin install agent-room@agent-room-marketplace
```

Reopen Claude Code in the project, then:

```text
/init-agents-space
```

Default: CLAUDE_01 + CODEX_EXPERT. For a new full room, use `/init-agents-space --mode full`:
CLAUDE_01, CODEX_01, CLAUDE_EXPERT, CODEX_EXPERT. If the bare alias conflicts with an existing skill,
use `/agent-room:init-agents-space`. Existing project files and custom room guides are preserved.

Continue talking to main: give a goal, ask for a simpler approach, request status or say "continue".
Main handles task scope, claims, peer requests, reviews and knowledge internally. Ordinary peer
conversation needs no task or fixed format. Only assigned writers edit their scope. Main asks for
material decisions or missing authority; it explains the smallest necessary human action when the
native host requires one. This guidance does not guarantee agent compliance or task success.

## Control when needed

```text
/agent-room:status
/agent-room:stop
/agent-room:start
/agent-room:doctor
```

Status/doctor are read-only. Stop preserves unfinished work; manual stop persists until start.
Reopening main may resume the saved native sessions. Changes to mode require stop and explicit
handoff of affected open tasks. No silent replacement session or team expansion is permitted.

## Agent/operator references

`agent-room guide` lists collaboration, learning, evidence and CLI references. Read a relevant topic
on demand, such as `agent-room guide collaboration`. Local custom rules remain in force after an
upgrade; the reference identifies this CLI's version and source hash. See [contracts](docs/v1.1.md)
for upgrade, permissions, errors and recovery. Source/runtime context already loaded into a native
session is not automatically replaced by this archive.

Selective knowledge/note/history search and optional inbox previews keep full-source read paths.
Check conditions, evidence and current revisions before reuse. Keep receipts and processing evidence
separate from transport success. Memory and peer text do not grant authority. Provider spending,
credentials, scope expansion and shipping retain their existing approval requirements.

To inspect this package without a room or provider tools:

```sh
python3 bin/agent-room --json verify-package /path/to/agent-room.zip
```

The verifier checks archive structure and hashes, not publisher authenticity. Default CLI contracts,
schema, native permissions and model loops are unchanged in 0.3.20.
