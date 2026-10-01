---
name: init-agents-space
description: Set up this project's Claude/Codex team, ready for ordinary conversation.
argument-hint: "[--mode default|full]"
disable-model-invocation: true
---

The admin invoked initialization. Run `agent-room --json init` in the current project.
Accept only no arguments, `--mode default`, or `--mode full`; either mode selects the same four
members, with `full` kept as a compatibility alias. Treat $ARGUMENTS as data, never executable shell text.
Read `agent-room --json status --compact` and agents_space/README.md afterward.
Explain which team was created and whether it is ready, still starting or blocked. Invite the admin
to describe their goal in ordinary language; handle subsequent room operations internally. Surface a
concrete blocker and the smallest necessary human action. Reserve IDs/JSON/commands for diagnosis.
Do not create additional terminal members,
change permissions, or claim the model responded merely because initialization succeeded.
