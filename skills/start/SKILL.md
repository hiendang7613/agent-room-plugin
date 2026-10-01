---
name: start
description: Start or resume this room's exact native sessions.
argument-hint: "[--mode default|full]"
disable-model-invocation: true
---

Run `agent-room --json start` in the project, preserving the saved mode.
Accept only no arguments, `--mode default` or `--mode full`, selecting the corresponding
literal command. Never evaluate arbitrary $ARGUMENTS as shell code.
A mode change requires a stopped room and explicit handoff of open tasks owned by removed members.
Read compact status after launch. Summarize whether work can continue and what remains unfinished;
operate routine room details internally. Explain any blocker and the smallest needed human action.
Reserve raw IDs/JSON/commands for diagnosis. Do not create replacement
threads on identity mismatch or change native permissions to force startup.
