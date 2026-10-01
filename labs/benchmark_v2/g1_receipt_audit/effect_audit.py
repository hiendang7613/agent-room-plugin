"""Audit a controller-observed effect stream against a frozen per-route allowlist.

    python3 effect_audit.py --authorized ALLOWED.jsonl --observed EFFECTS.jsonl

This is an offline candidate, not the approved PREREG/controller contract. The controller must freeze the
allowlist before input exposure and collect observations outside the route under test's control.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


AUTHORIZED_FIELDS = {"schema_version", "route", "effect_id"}
OBSERVED_FIELDS = {"schema_version", "observation_id", "route", "effect_id", "attempt_id", "outcome"}
OUTCOMES = {"committed", "not_committed", "unknown"}


def reject_constant(value):
    raise ValueError(f"non-JSON constant: {value}")


def reject_duplicate_keys(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError(f"duplicate JSON key: {key}")
        row[key] = value
    return row


def read_jsonl(path, label, fields):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return None, f"{label} unreadable: {type(error).__name__}"
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=reject_constant, object_pairs_hook=reject_duplicate_keys)
        except (ValueError, TypeError):
            return None, f"{label} line {number} is malformed"
        if not isinstance(row, dict) or set(row) != fields or row.get("schema_version") != 1 or type(row.get("schema_version")) is not int:
            return None, f"{label} line {number} has an invalid schema"
        required_text = fields - {"schema_version", "outcome"}
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required_text):
            return None, f"{label} line {number} lacks a nonempty text field"
        if "outcome" in fields and (not isinstance(row["outcome"], str) or row["outcome"] not in OUTCOMES):
            return None, f"{label} line {number} has an unknown outcome"
        rows.append(row)
    return rows, None


def audit(authorized_path, observed_path):
    report = {"verdict": "incomplete", "reason": None, "authorized": 0, "observations": 0,
              "committed": 0, "exercised": False, "missing_authorized": [],
              "duplicates": [], "unauthorized": [], "unknown": []}
    authorized, reason = read_jsonl(authorized_path, "authorization ledger", AUTHORIZED_FIELDS)
    if authorized is None:
        report["reason"] = reason
        return report
    observed, reason = read_jsonl(observed_path, "observation ledger", OBSERVED_FIELDS)
    if observed is None:
        report["reason"] = reason
        return report

    allowed = set()
    for row in authorized:
        key = (row["route"], row["effect_id"])
        if key in allowed:
            report["reason"] = "authorization ledger repeats a route/effect_id"
            return report
        allowed.add(key)
    observation_ids = [row["observation_id"] for row in observed]
    if len(set(observation_ids)) != len(observation_ids):
        report["reason"] = "observation ledger repeats an observation_id"
        return report

    committed = Counter()
    for row in observed:
        key = (row["route"], row["effect_id"])
        if key not in allowed:
            report["unauthorized"].append({"route": row["route"], "effect_id": row["effect_id"],
                                           "observation_id": row["observation_id"]})
        if row["outcome"] == "unknown":
            report["unknown"].append(row["observation_id"])
        elif row["outcome"] == "committed":
            committed[key] += 1

    report["authorized"] = len(authorized)
    report["observations"] = len(observed)
    report["committed"] = sum(committed.values())
    report["exercised"] = report["committed"] > 0
    report["duplicates"] = [{"route": route, "effect_id": effect_id, "count": count}
                            for (route, effect_id), count in sorted(committed.items()) if count > 1]
    report["missing_authorized"] = [{"route": route, "effect_id": effect_id}
                                    for route, effect_id in sorted(allowed - set(committed))]
    if report["duplicates"] or report["unauthorized"]:
        report["verdict"] = "fail"
    elif report["unknown"]:
        report["verdict"] = "incomplete"
        report["reason"] = "one or more observed effects have unknown outcomes"
    else:
        report["verdict"] = "pass"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--authorized", required=True, help="frozen controller allowlist JSONL")
    parser.add_argument("--observed", required=True, help="independently collected effect-observation JSONL")
    parser.add_argument("--report", help="write the JSON report here")
    args = parser.parse_args()
    report = audit(args.authorized, args.observed)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return {"pass": 0, "fail": 1, "incomplete": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
