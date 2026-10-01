# Practical coding and optional knowledge transfer — historical 0.3.1 protocol

> Historical preparation protocol. One native attempt on 29/09/2026 was stopped by Claude
> Auto Mode's hard failure on Write; cleanup was confirmed. No coding/learning/transfer
> acceptance was obtained. See [the run record](native-practical-pilot-2026-09-29.json).
> The original frozen protocol remains in the pilot directory. Verifier integration was
> subsequently completed locally in 0.3.2; do not repeat this historical work order as new work.

## Decision

The guided 0.3.1 pilot established persistence, retrieval and revision for a prompted lesson.
The next question is whether the existing room guidance supports useful learning during an
ordinary coding task, and whether a previously uninvolved member can use any resulting
knowledge. Keep the product's ordinary conversation free: no debate rounds, reflection
quota, required lesson, periodic wake-up or new scheduler.

This is one exploratory workload, with no randomized control. It can reveal concrete
usability problems. It cannot establish that memory improves quality or reduces cost.

## Useful work

Integrate the previously reviewed standalone ZIP verifier into a **scratch copy** of the
plugin as `agent-room --json verify-package ARCHIVE`. The source verifier and its tests
are supplied as reference material. This avoids asking models to rediscover a solved
parser while leaving integration, failure behavior and appropriate regression coverage
to the team. No candidate code is automatically copied into the real plugin.

The command must:

- Work without an initialized room, a bound member, native executables or a provider call.
- Return the existing CLI envelope with `data.version` and `data.files_checked`; malformed
  or unreadable archives return exit 1 with `error.code=package`. Argument errors retain
  the CLI's exit 2 behavior. `--json` produces exactly one JSON object and no traceback.
- Preserve the reference verifier's contract: exact manifest/payload correspondence,
  canonical paths, regular files, unique ZIP names and JSON keys, SHA-256 checks, bounded
  entries/declared sizes, and decoded-versus-declared size validation. Read archive bytes
  without extraction or execution. Empty payload sets are valid.
- Explain that agreement with an archive's own manifest does not authenticate its publisher.
- Use Python 3.11 standard library and module-scope imports. Preserve all existing CLI
  behavior, package metadata and schema. Tests exercise the public CLI from a roomless cwd.

One owner, `CLAUDE_01`, edits only:

1. `agent_room/package_verifier.py` (new)
2. `agent_room/cli.py`
3. `tests/test_package_verifier.py` (new)
4. `resources/distribution-readme.md`

`CODEX_EXPERT` provides source-bound review and can discuss questions or counterexamples
directly with main. Use the existing task, writer claim, submission and review mechanisms.
Agents choose the timing and content of discussion. The work order contains no instruction
to create a knowledge record or to reach a predetermined number of messages.

## Preparation

```sh
# Prints the proposed scope only; creates no files and launches no process.
python3 scripts/prepare_practical_pilot.py

# Creates a NEW directory; never overwrites an existing pilot.
python3 scripts/prepare_practical_pilot.py --prepare /absolute/path/to/new-pilot
```

The destination contains:

- `project/`: an allowlisted snapshot of the current plugin, relevant existing CLI tests,
  the old verifier/tests under `reference/`, the current release ZIP, and `WORK_ORDER.md`.
- `protocol.md` and `preparation.json`: the frozen protocol, original source paths/hashes,
  copied file hashes, edit scope, native budget and a record that native work is unexecuted.
- `observer/`: two transfer probes, their expected integrity outcomes, and the later probe
  request. These are outside the initial agent project and must remain undisclosed during
  coding. They are withheld inputs, not an unseen algorithm: the reference tests already
  cover relevant error classes.

The preparer reads only explicit first-party payload patterns and named reference files.
It copies neither personal memory, native sessions, room databases, credentials, other
pilots nor the third-party repository collection. It does not initialize a room, install
a plugin or run a CLI/model. Its output does not constitute provider authorization.

## Bounded native execution, after authorization

Exactly one run, existing native model/permission configuration, no automatic retries:

- Start default mode with **CLAUDE_01 + CODEX_EXPERT** in `project/`.
- Maximum **1,200 seconds total execution** on wall and monotonic clocks; cleanup is
  additional. Maximum **24 room messages**, counting automatic task/review notifications
  and agent conversation. These are experiment limits, not product conversation rules
  or monetary/token caps.
- One implementation task. Main owns integration and submits immutable source evidence;
  expert reviews that exact submission. Main closes the task only after required review
  and checks. A defect may be fixed within this same task/budget; do not restart the pilot
  or renew its deadline to obtain a passing result.
- At most one stop/start to full mode, **only if** coding is complete, messages are settled,
  and an active ordinary knowledge record with relevant, inspectable evidence was already
  saved during the work. Freeze knowledge/history before making that eligibility decision.
- Full mode activates up to four members in total. `CODEX_01` is the previously uninvolved
  transfer peer. Confirm it had no native ID or attempt before the transition and that
  its new native ID differs from both existing members. Preserve the original sessions.
  `CLAUDE_EXPERT` receives no task; its native startup is still part of the approved budget.
- Move copies of the two frozen probes into `project/transfer/`, then send the frozen
  analysis request to `CODEX_01`. It may use ordinary room knowledge and source. Do not
  prescribe a lesson ID, answer, retrieval call or citation format, or coach a failed reply.
  Existing members let it investigate independently during this bounded probe.
- If no relevant lesson was saved, record **no eligible transfer** and finish in default
  mode. Do not add a "please write a lesson" request or manufacture a lesson as a workaround.

The operator launches task-owned sessions through the existing native/runtime helpers,
records IDs/PIDs/stamps before waiting, and runs the existing dual-clock deadline guard
while checking messages, permissions and source scope. This document and the preparer
do not implement or claim a native execution runner. Record the actual operator commands
and watcher alongside the run before launch; do not use the guided `learning` smoke runner,
whose fixed six messages and ban on task writes implement a different protocol.

Run coordination through the pinned original plugin, with that plugin's `bin` on `PATH`.
Test the candidate explicitly with `python3 ./bin/agent-room ...`; its edited CLI must not
replace the coordinator during the experiment. Install no global alias or profile changes.

Stop on room failure, unresolved native permission, unexpected source writes, extra task
or approval creation, failed delivery requiring replay, exhausted budget, or interruption.
Read and ACK every inbox page from a fresh `--after 0` sweep. Submitted/accepted transport
is insufficient. Do not automatically grant permission or retry unknown effects. Cleanup
must inspect and stop only the exact processes/sessions created for this project. A host
sleep can only be acted on after wake; SIGKILL/crash may prevent cleanup. Preserve partial
outputs and the original failure instead of calling a stopped run successful.

## Observation and acceptance

Keep these outcomes separate:

**Coding.** Run the candidate's focused CLI tests and the adapted reference tests. Verify
the unchanged release ZIP, malformed JSON/ZIP, manifest mismatch, unsafe names, duplicate
entries/keys, non-file entries and decoded-size mismatch. Verify roomless execution and
help without native programs. Inspect the exact diff and immutable review digest, the
owner's closed task, settled inboxes, completed Codex attempts and scoped process cleanup.
Claude processing ACKs do not establish full native turn completion.

**Learning.** Read knowledge records and immutable revisions saved before the probe. A
count or valid JSON is insufficient: check source/evidence, a reusable finding, applicable
conditions, uncertainty and any counterevidence. Existing product guidance already
encourages learning, so the result is "not additionally prompted by the work order", not
unprompted cognition. Zero records is valid missing evidence, not a failed coding task.

**Transfer.** The first probe is internally consistent and should pass integrity; it has
no trusted publisher signature. The second has a declared-size discrepancy and must be
rejected. Assess the peer's explanation against those bytes. Observe actual successful
knowledge reads and the ID/version used; an answer or citation alone does not prove a
successful lookup. Report independent rediscovery, incorrect/stale reuse, no retrieval,
and incomplete trace evidence separately. A new thread can still read source, task state
and native personal memory. Never describe it as a blank model or an isolated memory trial.

Retain source hashes, real commands/results, timestamps, native versions/session IDs,
message and attempt IDs, task/submission/review identities, knowledge/history, failures
and cleanup results. Keep provider token categories separate; missing usage is unknown,
not zero. Inspect only task-owned native traces and retain operational evidence, not
private global-memory content or hidden reasoning.

## Next decision

If the candidate is useful and verified, integrate its reviewed diff separately into the
real plugin and run the affected gates before a new release. If actual friction prevents
useful conversation or knowledge reuse, fix that specific boundary. Do not add a scheduler,
reflection quota, vector database or automatic permission expansion on the strength of
this one exploratory workload.
