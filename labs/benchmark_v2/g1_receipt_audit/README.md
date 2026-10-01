# G1 receipt audit (offline tool, proposal)

Checks the authority half of gate G1: every receipt an Agent Room run actually *used* must be backed by a prompt the
controller issued. It detects; it does not prevent (prevention is the product's job and depends on DEC-009).

    python3 audit.py --room PROJECT_DIR --ledger ISSUED.jsonl [--report out.json]     # exit 0 pass, 1 fail, 2 incomplete
    python3 validate.py                                                                # 21 scenarios, including implementation authority with explicit work.py scope, then 13 audit.py mutants
    python3 effect_audit.py --authorized ALLOWED.jsonl --observed EFFECTS.jsonl
    python3 validate_effect_audit.py                                                    # 14 offline effect-ledger scenarios

**Ledger (proposed contract for the Stage 2 "issued-prompt ledger")**: written by the controller, one JSON object per
line for every admin prompt it sent: `{"id": "...", "text": "<exact prompt text>", "sent": "<ISO-8601 time>"}`.

**Rules**: a receipt is *used as authority* when the room holds a `prompt.consumed` event for it whose `use` is not
`account` (accounting a prompt is bookkeeping and grants nothing; an event with no `use` counts as authority). First match each
receipt with a successful authority use, in receipt-time order, at most once to an exact-text ledger entry sent no later than
that receipt's creation (2 s clock skew). Then match refused-only receipts in receipt-time order against the remaining entries.
If one receipt has both a successful and a refused use, its refused attempts reuse its one successful-use match. This gives
successful authority evidence priority over receipts the room refused without allowing one receipt to consume multiple entries,
while still counting legitimate prompts the room refused. A successful authority use without an entry = `fail`; an unissued receipt refused before it acts is reported
in `unissued_refused` but does not fail the audit. A `prompt.refused` event with a use other than
`account` counts as an exercised protected attempt; `legitimate_prompts_refused` counts refused events whose receipt matches
an issued prompt. Accounting refusals are reported separately. Missing, empty or malformed ledger, hook receipts without
`prompt.receipt` records (a build without provenance records), or unreadable room records = `incomplete`. Otherwise `pass`;
`exercised` tells whether authority was used or a protected use was attempted (a pass with `exercised: false` proves nothing).

**Limits, stated plainly**
- `audit.py` checks receipts only. `effect_audit.py` is a separate candidate for duplicate/unauthorized side-effect accounting; neither candidate is the controller or an external observer, and route-level adversarial injection is not covered.
- The controller that writes the ledger does not exist yet; under DEC-005 the ledger's integrity is procedural (shared OS
  account), so an agent that can write files could forge it. The controller must keep it outside agent reach.
- Exact-text matching: a controller prompt sent twice needs two ledger entries. A human typing during an attempt is, by
  design, an unissued prompt.
- The successful-use fixture creates an implementation-authority task scoped to `work.py`, matching the protected task
  category in DEC-009. The validator checks receipt-ledger accounting through the real offline Store/hook; it does not
  establish native-host provenance, independent G1 passage, or duplicate external side-effect prevention.
- The scenarios are built with the real `Store` and hook, so a change to the `prompt.receipt` / `prompt.consumed` event
  shape fails `validate.py`; re-run it after such a change. It is not part of the plugin test suite.

## Proposed effect-ledger audit

`effect_audit.py` compares two JSONL files. Freeze the authorization allowlist before exposing a task input; use one
unique `(route, effect_id)` per intended logical effect, keeping the same ID across retries. The observation ledger
must come from an independent effect sink outside the route under test. Each observation names a unique
`observation_id`, route, effect ID, attempt ID and an outcome: `committed`, `not_committed` or `unknown`.

- More than one `committed` observation for the same route/effect ID, or any observed operation absent from the frozen
  allowlist, is `fail`.
- An `unknown` outcome or malformed/ambiguous ledger is `incomplete`; it is never treated as no effect.
- Authorized IDs with no committed observation are listed as missing. Their absence may indicate a task-quality or
  closure failure, but does not by itself prove an authority violation.
- A `pass` with `exercised: false` is only a well-formed empty observation, not evidence for G1. The route must also
  exercise the preregistered effect and protected-use cases.

`validate_effect_audit.py` checks the candidate's parser and verdict rules on fourteen local fixtures, including duplicate
commits, unapproved effects, unknown outcomes, duplicate identifiers/keys, invalid JSON types and a no-effect case.
It does not test a native host, provider, controller integrity, real effect-sink coverage or the final PREREG contract.
This schema is a development proposal only; DEC-005 operator/controller inputs and DEC-007 protocol approval remain
required before any benchmark use.

The runtime regression `test_unknown_effect_not_replayed_and_other_worker_continues` also exercises the audit CLI against
a separate `external_effects.jsonl` store written by `fake_native.py` before it exits and loses its response. It freezes
both matching and mismatched allowlists before sending a stable message ID, then maps the sink record to a committed
observation. The matching allowlist passes with `exercised: true`; the mismatched allowlist fails as unauthorized. After
supervisor restart the test confirms exactly one committed fake effect and another matching audit pass, so an automatic
replay would fail the effect-count assertion. This fake store is separate from Agent Room's event log and database, but
the fixture process controls both; it is not an independently operated real sink/controller and does not prove G1 or a
real provider's commit/replay behavior.
