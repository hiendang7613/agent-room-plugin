#!/usr/bin/env python3
"""Explicitly opt-in live smoke check. Default prints scope and makes NO calls.

Uses an owned background Claude session as a main stand-in, not the user's sessions.
Does not prove interactive UI ergonomics, semantic task classification or full quality.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_room.common import PLUGIN_ROOT, RoomError, atomic_write, process_alive, process_stamp
from agent_room.common import fingerprint
from agent_room import __version__
from agent_room.native import claude_agents, owned_descendants, start_claude, stop_claude, stop_descendants
from agent_room.package import PATTERNS
from agent_room.store import Store
from scripts import learning_smoke


def review_messages_settled(store, task_id):
    """Receipts commit before ACK/turn completion; do not tear down that work."""
    with store.read() as db:
        messages = [dict(row) for row in db.execute("SELECT * FROM messages WHERE task=? ORDER BY seq", (task_id,))]
        attempts = [json.loads(row[0]) for row in db.execute("SELECT data FROM attempts WHERE task=?", (task_id,))]
    # Four protocol messages are expected; peers may also send a substantive
    # handoff. Every additional message must settle before teardown as well.
    if len(messages) < 4 or any(message["status"] != "processed" for message in messages):
        return False
    if len(attempts) != len(messages) or {a["message"] for a in attempts} != {m["id"] for m in messages}:
        return False
    return all(attempt["processed"] and (attempt["member"] == "CLAUDE_01" or attempt["state"] == "completed")
               for attempt in attempts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicit operator authorization to use native models/subscriptions")
    parser.add_argument("--project", type=Path, help="Existing empty, trusted scratch directory; never use a real project")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--scenario", choices=("lifecycle", "review", "learning"), default="lifecycle",
                        help="review checks source-bound reviews; learning checks save, reuse after resume and counterevidence")
    parser.add_argument("--max-seconds", type=float, default=600,
                        help="learning only: total execution limit on both wall and monotonic clocks; cleanup is additional")
    args = parser.parse_args()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be positive and finite")
    if not 0 < args.max_seconds < float("inf"):
        parser.error("--max-seconds must be positive and finite")
    scope = {"native_provider_calls": True, "script_dispatches": 4, "expected_peer_dispatches": 1,
             "max_persistent_members": 4, "project": "new task-owned temporary directory",
             "global_alias_install": False, "permission_auto_approval": False,
             "credentials_changes": False, "publishing": False,
             "note": "Model/tool steps and token cost depend on native settings; no fixed monetary cap is promised."}
    if args.scenario == "review":
        scope.update(scenario="review", script_dispatches=2, expected_peer_dispatches=2,
                     max_persistent_members=2, source_edits="one test-owned file written by the script; models review only",
                     note="Two task submissions trigger two expert reviews and normally two peer notifications to main. Native tool/model turns and token cost follow current settings.")
    elif args.scenario == "learning":
        scope.update(learning_smoke.SCOPE, max_seconds=args.max_seconds, wait_timeout_seconds=args.timeout,
                     time_limit="Both clocks, checked during operations; cleanup is additional. A suspended host can stop processes only after resume.")
    if not args.execute:
        print(json.dumps({"execute": False, "proposed_scope": scope}, indent=2))
        return 0
    project = args.project.resolve() if args.project else Path(tempfile.mkdtemp(prefix="agent-room-live-")).resolve()
    if not project.is_dir() or any(project.iterdir()):
        parser.error("--project must be an existing empty scratch directory")
    session = None
    env = dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_01",
               AGENT_ROOM_PROJECT=str(project),
               AGENT_ROOM_SKIP_ALIAS="1", CLAUDE_CODE_DISABLE_BG_EXIT_HANDOFF="1")
    env.pop("CLAUDE_CODE_MESSAGING_TOKEN", None)
    env.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    env.pop("CLAUDE_ENV_FILE", None)
    env.pop("AGENT_ROOM_SESSION_ID", None)
    env["PATH"] = str(PLUGIN_ROOT / "bin") + os.pathsep + env.get("PATH", "")
    source_files = sorted({str(path.relative_to(PLUGIN_ROOT))
                           for pattern in (*PATTERNS, "scripts/native_smoke.py", "scripts/learning_smoke.py")
                           for path in PLUGIN_ROOT.glob(pattern)})
    report = {"project": str(project), "scope": scope, "checks": [], "messages": [], "status": "running", "plugin_version": __version__,
              "source_sha256": fingerprint(PLUGIN_ROOT, source_files)}
    store = Store(project)
    budget = learning_smoke.Deadline(args.max_seconds) if args.scenario == "learning" else None
    cleaning = False
    before_main = None
    main_started = False
    main_process = None
    def guard():
        if budget and not cleaning:
            budget.check()
            if store.exists():
                with store.read() as db:
                    if db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > learning_smoke.SCOPE["max_room_messages"]:
                        raise RuntimeError("Learning pilot message budget exceeded")
                    if any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in ("tasks", "notes", "approvals")):
                        raise RuntimeError("Learning pilot changed task, decision or approval state")
    def command(*command_args, text=None):
        guard()
        result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), "--project", str(project),
                                 *command_args], env=env, cwd=project, input=text, text=True, capture_output=True, timeout=35)
        guard()
        data = json.loads(result.stdout)
        if result.returncode or not data.get("ok"):
            raise RuntimeError(str(data))
        return data["data"]
    def wait(predicate, label):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            guard()
            if predicate():
                guard()
                report["checks"].append({"check": label, "passed": True})
                print(label + ": passed", flush=True)
                return
            if store.exists() and (store.room()["status"] == "failed" or any(
                item["state"] == "pending" for item in store.status()["approvals"])):
                raise RuntimeError("Native room failed or requires human permission; inspect retained status. No approval was supplied.")
            time.sleep(.5)
        raise RuntimeError("Timed out: " + label)
    def send_and_check(member, peer=False):
        text = ("Authorized isolated integration smoke. Do not modify source, create tasks, spawn agents or call external services. "
                "Run agent-room inbox, identify THIS message, and use agent-room ack with evidence 'native smoke received'. "
                "Do not send ACK-only messages. ")
        if peer:
            text += ("For this one transport check only, then use agent-room send --to CLAUDE_01 --body with a quoted inline argument (no heredoc) to send ONE substantive result: "
                     "'Native roundtrip evidence: CODEX_EXPERT received and processed the smoke request. "
                     "CLAUDE_01: acknowledge this peer message via agent-room ack, then stop without sending another peer message.'")
        message = command("send", "--to", member, text=text)
        report["messages"].append({"id": message["id"], "recipient": member})
        atomic_write(project / "native-smoke-report.json", json.dumps(report, indent=2))
        def processed():
            with store.read() as db:
                return db.execute("SELECT status FROM messages WHERE id=?", (message["id"],)).fetchone()[0] == "processed"
        wait(processed, member + " native message processing")
    def review_scenario():
        artifact = project / "sample.txt"
        artifact.write_text("Agent Room review fixture revision one\n")
        receipt = store.intake(session, "Authorized isolated review smoke: inspect sample.txt and record source-bound review receipts. No source edits or external services by models.", origin="smoke_operator")
        task = command("task", "create", text=json.dumps({"title": "Review fixture", "request": "Read sample.txt; review only. Read the evidence guide, record an approve receipt if the file is a single nonempty line, then ACK the task message. Do not edit source, spawn agents or call external services.",
            "acceptance": "sample.txt has one nonempty line; review records the exact submission digest", "next": "Wait for expert review",
            "owner": "CLAUDE_01", "source": receipt, "authority": "analysis", "scope": ["sample.txt"],
            "review_policy": "peer_required", "reviewer": "CODEX_EXPERT"}))
        def submit():
            current = command("task", "show", task["id"])
            submission = command("task", "submit", task["id"], "--expected-version", str(current["version"]), text=json.dumps({
                "paths": ["sample.txt"], "summary": "Script wrote a review fixture", "evidence": ["Script verified a single nonempty line"]}))["submission"]
            report.setdefault("submissions", []).append(submission["id"])
            atomic_write(project / "native-smoke-report.json", json.dumps(report, indent=2))
            wait(lambda: next(t for t in store.status()["tasks"] if t["id"] == task["id"])["review_status"]["state"] == "approved", "source-bound peer review " + submission["id"])
            return submission
        first = submit()
        artifact.write_text("Agent Room review fixture revision two\n")
        current = command("task", "show", task["id"])
        denied = subprocess.run([sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), "--project", str(project), "task", "update", task["id"],
            "--expected-version", str(current["version"])], env=env, cwd=project, input='{"state":"done"}', text=True, capture_output=True, timeout=15)
        if denied.returncode == 0 or json.loads(denied.stdout).get("error", {}).get("code") != "review_required":
            raise RuntimeError("Stale review incorrectly completed the task")
        report["checks"].append({"check": "stale native review rejected", "passed": True})
        second = submit()
        if first["digest"] == second["digest"]:
            raise RuntimeError("Different submissions have the same digest")
        wait(lambda: review_messages_settled(store, task["id"]), "both review requests and peer notifications processed; Codex turn completed")
        with store.read() as db:
            report["messages"] = [dict(row) for row in db.execute(
                "SELECT id,sender,recipient,status,detail FROM messages WHERE task=? ORDER BY seq", (task["id"],))]
        report["attempts_before_restart"] = store.attempts(task["id"])["items"]
        current = command("task", "show", task["id"])
        checkpoint = command("task", "checkpoint", task["id"], "--expected-version", str(current["version"]), text=json.dumps({
            "summary": "Two native reviews received", "last_safe_action": "Verified the second receipt", "next": "Complete after source verification",
            "unknown_effects": [], "paths": ["sample.txt"]}))
        current = command("task", "show", task["id"])
        command("task", "update", task["id"], "--expected-version", str(current["version"]), text='{"state":"done"}')
        original_id = store.member("CODEX_EXPERT")["native_id"]
        command("stop")
        command("start")
        wait(lambda: store.room()["status"] == "running", "review room exact restart")
        pack = command("task", "context", task["id"])
        if store.member("CODEX_EXPERT")["native_id"] != original_id or not any("generation changed" in reason for reason in pack["checkpoint_reconcile"]):
            raise RuntimeError("Checkpoint/session reconciliation not observed after restart")
        report["checkpoint"] = checkpoint["id"]
        report["checks"].append({"check": "checkpoint retained with explicit generation reconciliation", "passed": True})
    if budget:
        budget.arm()
    try:
        if budget:
            before_main = {agent["sessionId"] for agent in claude_agents(project)}
        main_started = True
        native = asyncio.run(start_claude(project, None, False, env, project / "native-main-launch.log",
                member="AGENT_ROOM_SMOKE_MAIN", instructions=
                "This is an authorized isolated Agent Room integration smoke. You are CLAUDE_01. "
                "Process native peer messages with agent-room ack only; do not invent work, change settings or grant permissions."))
        session = native["sessionId"]
        main_process = (native["pid"], process_stamp(native["pid"]))
        report["main_session"] = session
        env["AGENT_ROOM_SESSION_ID"] = session
        command("init")
        wait(lambda: store.room()["status"] == "running", "default native startup")
        if args.scenario == "review":
            review_scenario()
        elif args.scenario == "learning":
            learning_smoke.run(store, command, wait, report)
        else:
            original = store.member("CODEX_EXPERT")["native_id"]
            send_and_check("CODEX_EXPERT", peer=True)
            def roundtrip():
                with store.read() as db:
                    return bool(db.execute("SELECT 1 FROM messages WHERE sender='CODEX_EXPERT' AND recipient='CLAUDE_01' AND status='processed'").fetchone())
            wait(roundtrip, "Codex to Claude roundtrip processing")
            command("stop")
            command("start")
            wait(lambda: store.room()["status"] == "running", "default native restart")
            if store.member("CODEX_EXPERT")["native_id"] != original:
                raise RuntimeError("Codex resumed a different thread")
            send_and_check("CODEX_EXPERT")
            command("stop")
            command("start", "--mode", "full")
            wait(lambda: store.room()["status"] == "running", "full native startup")
            send_and_check("CLAUDE_EXPERT")
            send_and_check("CODEX_01")
        stop_claude(project, session)
        wait(lambda: store.room()["status"] == "stopped", "owner exit stops native workers")
        def native_processes_stopped():
            status = command("status")
            return status["supervisor_alive"] is False and all(member["process_alive"] is False for member in status["members"])
        wait(native_processes_stopped, "owned native process exit confirmed")
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as exc:
        if budget:
            budget.disarm()
        report.update(status="failed", error=str(exc) or type(exc).__name__)
        if isinstance(exc, RoomError):
            report["error_details"] = exc.details
    finally:
        cleaning = True
        if budget:
            budget.disarm()
            report["execution_elapsed"] = budget.observation()
        cleanup_started = time.monotonic()
        # A deadline may interrupt --bg before it returns a session ID. First
        # stop this script's own launch descendants so they cannot spawn late.
        if budget and main_started and not session:
            try:
                asyncio.run(stop_descendants(owned_descendants(os.getpid())))
            except Exception as exc:
                report["launch_cleanup_error"] = str(exc)
            try:
                created = [agent for agent in claude_agents(project)
                           if agent.get("sessionId") not in (before_main or set())
                           and Path(agent.get("cwd", "/nonexistent")).resolve() == project
                           and agent.get("kind") == "background"]
                report["partial_launch_sessions"] = [agent["sessionId"] for agent in created]
                for agent in created:
                    stop_claude(project, agent["sessionId"])
            except Exception as exc:
                report["partial_launch_cleanup_error"] = str(exc)
        if report.get("error_details"):
            for created in report["error_details"].get("reported_new_ids", []):
                try:
                    stop_claude(project, created)
                except Exception as cleanup:
                    report["copy_cleanup_error"] = str(cleanup)
        if store.exists():
            try:
                command("stop")
            except Exception as exc:
                report["cleanup_error"] = str(exc)
                report["status"] = "failed"
        if session:
            try:
                stop_claude(project, session)
            except Exception as exc:
                report["main_cleanup_error"] = str(exc)
                report["status"] = "failed"
        if store.exists():
            try:
                report["final_status"] = command("status")
            except Exception as exc:
                report["status_read_error"] = str(exc)
                report["status"] = "failed"
        if budget:
            try:
                cleanup_deadline = time.monotonic() + 5
                while True:
                    alive = [agent["sessionId"] for agent in claude_agents(project)
                             if agent.get("sessionId") not in (before_main or set())
                             and Path(agent.get("cwd", "/nonexistent")).resolve() == project
                             and process_stamp(agent.get("pid"))]
                    status = command("status") if store.exists() else None
                    if status is not None:
                        report["final_status"] = status
                    stopped = (not alive and (not main_process or not process_alive(*main_process))
                               and (not store.exists() or (status is not None and status["supervisor_alive"] is False
                                    and all(member["process_alive"] is False for member in status["members"]))))
                    if stopped or time.monotonic() >= cleanup_deadline:
                        break
                    time.sleep(.1)
                report["cleanup"] = {"confirmed": stopped, "remaining_claude_sessions": alive,
                                     "seconds": time.monotonic() - cleanup_started}
                if not stopped:
                    report.update(status="failed", cleanup_error="Owned native process exit not confirmed")
                if store.exists():
                    with store.read() as db:
                        report["retained_learning_state"] = {
                            "knowledge": [json.loads(row[0]) for row in db.execute("SELECT data FROM knowledge")],
                            "revisions": [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE kind='knowledge.revised' ORDER BY seq")],
                            "attempts": [json.loads(row[0]) for row in db.execute("SELECT data FROM attempts ORDER BY rowid")],
                            "messages": [dict(row) for row in db.execute("SELECT id,sender,recipient,body,status FROM messages ORDER BY seq")],
                        }
            except Exception as exc:
                report.update(status="failed", cleanup_verification_error=str(exc))
        atomic_write(project / "native-smoke-report.json", json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": report["status"], "report": str(project / 'native-smoke-report.json')}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
