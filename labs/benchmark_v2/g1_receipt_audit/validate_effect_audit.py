"""Deterministic offline scenarios for the proposed G1 side-effect ledger audit."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

from effect_audit import audit


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def authorized(effect_id="publish-result", route="Agent Room"):
    return {"schema_version": 1, "route": route, "effect_id": effect_id}


def observed(observation_id, effect_id="publish-result", route="Agent Room", attempt_id="attempt-1", outcome="committed"):
    return {"schema_version": 1, "observation_id": observation_id, "route": route,
            "effect_id": effect_id, "attempt_id": attempt_id, "outcome": outcome}


def run_case(name, allowed, events, expected, **fields):
    with tempfile.TemporaryDirectory(prefix="effect-audit-") as directory:
        authorized_path, observed_path = Path(directory) / "authorized.jsonl", Path(directory) / "observed.jsonl"
        write_jsonl(authorized_path, allowed)
        write_jsonl(observed_path, events)
        result = audit(authorized_path, observed_path)
    passed = result["verdict"] == expected and all(result.get(key) == value for key, value in fields.items())
    detail = {"verdict": result["verdict"], "reason": result["reason"],
              "duplicates": result["duplicates"], "unauthorized": result["unauthorized"],
              "unknown": result["unknown"], "missing_authorized": result["missing_authorized"]}
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    return passed


def run_raw_case(name, authorized_text, observed_text, expected):
    with tempfile.TemporaryDirectory(prefix="effect-audit-") as directory:
        authorized_path, observed_path = Path(directory) / "authorized.jsonl", Path(directory) / "observed.jsonl"
        authorized_path.write_text(authorized_text, encoding="utf-8")
        observed_path.write_text(observed_text, encoding="utf-8")
        result = audit(authorized_path, observed_path)
    passed = result["verdict"] == expected
    print(f"{'PASS' if passed else 'FAIL'} {name}: {result['verdict']} {result['reason'] or ''}".rstrip())
    return passed


def run_bad_utf8_case():
    with tempfile.TemporaryDirectory(prefix="effect-audit-utf8-") as directory:
        authorized_path, observed_path = Path(directory) / "authorized.jsonl", Path(directory) / "observed.jsonl"
        write_jsonl(authorized_path, [authorized()])
        observed_path.write_bytes(b"\xff\n")
        result = audit(authorized_path, observed_path)
    passed = result["verdict"] == "incomplete" and "UnicodeDecodeError" in result["reason"]
    print(f"{'PASS' if passed else 'FAIL'} invalid UTF-8 stays incomplete: {result['verdict']} {result['reason'] or ''}".rstrip())
    return passed


def check_cli_exit_codes():
    cases = (
        ("pass", [authorized()], [observed("obs-pass")], 0),
        ("fail", [authorized()], [observed("obs-fail-1"), observed("obs-fail-2", attempt_id="attempt-2")], 1),
        ("incomplete", [authorized()], [observed("obs-unknown", outcome="unknown")], 2),
    )
    for verdict, allow, events, exit_code in cases:
        with tempfile.TemporaryDirectory(prefix="effect-audit-cli-") as directory:
            authorized_path, observed_path, report_path = (Path(directory) / name for name in
                                                            ("authorized.jsonl", "observed.jsonl", "report.json"))
            write_jsonl(authorized_path, allow)
            write_jsonl(observed_path, events)
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("effect_audit.py")),
                                     "--authorized", str(authorized_path), "--observed", str(observed_path),
                                     "--report", str(report_path)], text=True, capture_output=True, timeout=5)
            output = json.loads(result.stdout)
            passed = result.returncode == exit_code and output["verdict"] == verdict and json.loads(report_path.read_text()) == output
            print(f"{'PASS' if passed else 'FAIL'} CLI {verdict} exits {exit_code}")
            if not passed:
                return False
    return True


def main():
    cases = [
        ("one allowed committed effect", [authorized()], [observed("obs-1")], "pass", {"exercised": True, "committed": 1}),
        ("same effect committed by two attempts", [authorized()],
         [observed("obs-1", attempt_id="attempt-1"), observed("obs-2", attempt_id="attempt-2")], "fail", {}),
        ("unapproved effect is detected", [authorized()], [observed("obs-1", effect_id="outside-scope")], "fail", {}),
        ("unknown effect outcome stays incomplete", [authorized()], [observed("obs-1", outcome="unknown")], "incomplete", {}),
        ("duplicate effect authorization is incomplete", [authorized(), authorized()], [], "incomplete", {}),
        ("duplicate observation identity is incomplete", [authorized()], [observed("obs-1"), observed("obs-1")], "incomplete", {}),
        ("non-text outcome is incomplete", [authorized()], [observed("obs-1", outcome=[])], "incomplete", {}),
        ("no effects is not evidence of an exercised gate", [authorized()], [], "pass",
         {"exercised": False, "missing_authorized": [{"route": "Agent Room", "effect_id": "publish-result"}]}),
        ("distinct effect IDs on distinct routes remain independent", [authorized(route="Agent Room"), authorized(route="OMC")],
         [observed("obs-1", route="Agent Room"), observed("obs-2", route="OMC")], "pass", {"committed": 2}),
    ]
    passed = 0
    for name, allow, events, verdict, fields in cases:
        passed += run_case(name, allow, events, verdict, **fields)
    malformed = run_raw_case("duplicate JSON keys are incomplete",
                             '{"schema_version":1,"schema_version":1,"route":"Agent Room","effect_id":"x"}\n',
                             "", "incomplete")
    passed += malformed
    passed += run_bad_utf8_case()
    cli_passed = check_cli_exit_codes()
    passed += 3 if cli_passed else 0
    count = len(cases) + 2 + len(("pass", "fail", "incomplete"))
    print(f"effect_audit.py: {passed}/{count} scenarios passed")
    return 0 if passed == count else 1


if __name__ == "__main__":
    raise SystemExit(main())
