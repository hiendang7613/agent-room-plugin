"""Receipts whose host provenance is confirmed as human, for tests that let an admin prompt act on authority.

DEC-009: Store.source refuses an unverified or absent receipt for the protected uses, so a receipt made with a bare
intake() cannot approve, delegate implementation or write admin knowledge. human_receipt() writes a real transcript
that holds a human row for the prompt and records where it ended, exactly as the hook does.
"""

import itertools
import json

_files = itertools.count(1)


def human_receipt(store, body, session="main"):
    path = store.runtime / f"transcript-{next(_files)}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": body},
                                "origin": {"kind": "human"}}) + "\n")
    return store.intake(session, body, provenance={"transcript": str(path), "offset": path.stat().st_size, "hook": {"state": "human"}})
