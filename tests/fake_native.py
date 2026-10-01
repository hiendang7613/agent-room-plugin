#!/usr/bin/env python3
"""Local CLI boundary fixture: records effects, never calls any model or network."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


ROOT = Path(os.environ["FAKE_NATIVE_ROOT"])
ROOT.mkdir(parents=True, exist_ok=True)


def emit(value):
    print(json.dumps(value), flush=True)


def record(kind, data):
    with open(ROOT / "effects.jsonl", "a") as stream:
        stream.write(json.dumps({"kind": kind, "data": data, "member": os.environ.get("AGENT_ROOM_MEMBER")}) + "\n")


def commit_external_effect(effect_id):
    """Persist a fake downstream commit separately from the native event log."""
    with open(ROOT / "external_effects.jsonl", "a") as stream:
        stream.write(json.dumps({"schema_version": 1, "observation_id": str(uuid.uuid4()),
                                 "route": "Agent Room", "effect_id": effect_id,
                                 "outcome": "committed"}) + "\n")


def daemon(session, project):
    sockets = ROOT / "sockets"
    sockets.mkdir(mode=0o700, exist_ok=True)
    path = sockets / f"{os.getpid()}.sock"
    registry = ROOT / (session + ".agent.json")
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen()
    server.settimeout(.2)
    registry.write_text(json.dumps({"sessionId": session, "id": session[:8], "kind": "background",
                                   "cwd": project, "pid": os.getpid(), "status": "done"}))
    active = True
    def stop(_signum, _frame):
        nonlocal active
        active = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while active:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                record("claude_inbox", json.loads(connection.makefile().readline()))
    finally:
        server.close()
        path.unlink(missing_ok=True)
        registry.write_text(json.dumps({"sessionId": session, "cwd": project, "status": "stopped"}))


def app_server():
    thread = None
    active_turn = None
    pending = None
    for line in sys.stdin:
        packet = json.loads(line)
        method = packet.get("method")
        params = packet.get("params", {})
        request_id = packet.get("id")
        record("codex_packet", packet)
        if not method:
            if pending is not None and request_id == pending:
                if packet["result"].get("decision") == "accept":
                    record("approved_effect", {"request": pending})
                emit({"method": "serverRequest/resolved", "params": {"requestId": pending, "threadId": thread}})
                emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": active_turn, "status": "completed"}}})
                pending = None
            continue
        if method == "initialized":
            continue
        if method == "initialize":
            emit({"id": request_id, "result": {"userAgent": "fake-native-contract-fixture"}})
        elif method in {"thread/start", "thread/resume"}:
            thread = params.get("threadId") or str(uuid.uuid4())
            if method == "thread/resume" and not (ROOT / (thread + ".thread")).exists():
                emit({"id": request_id, "error": {"code": -1, "message": "Unknown native thread"}})
                continue
            (ROOT / (thread + ".thread")).write_text("persisted")
            emit({"id": request_id, "result": {"thread": {"id": thread, "turns": []}, "approvalPolicy": "on-request"}})
        elif method in {"turn/start", "turn/steer"}:
            text = params["input"][0]["text"]
            if "crash after input" in text:
                effect_id = params.get("clientUserMessageId")
                if effect_id:
                    commit_external_effect(effect_id)
                record("unknown_effect", {"message": effect_id})
                os._exit(7)
            if "spawn owned child" in text:
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                record("owned_child", {"pid": child.pid})
            if method == "turn/steer" and params.get("expectedTurnId") != active_turn:
                emit({"id": request_id, "error": {"code": -1, "message": "Wrong active turn"}})
                continue
            active_turn = active_turn or str(uuid.uuid4())
            if "send peer result" in text:
                result = subprocess.run(["agent-room", "send", "--to", "CLAUDE_01"],
                    input="Fixture peer finding; source remains unchanged", text=True, capture_output=True)
                record("peer_cli", {"returncode": result.returncode, "result": result.stdout})
            emit({"id": request_id, "result": {"turn": {"id": active_turn, "status": "inProgress"}}})
            emit({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": active_turn, "status": "inProgress"}}})
            if "needs approval" in text:
                pending = "approval-42"
                emit({"id": pending, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": thread, "turnId": active_turn, "itemId": "item-42", "startedAtMs": 1,
                    "command": "echo approved", "cwd": os.getcwd()}})
            elif "keep busy" not in text:
                emit({"method": "item/completed", "params": {"threadId": thread, "item": {"id": "reply", "type": "agentMessage", "text": "FAKE response; no provider called"}}})
                emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": active_turn, "status": "completed"}}})
                active_turn = None
        elif method == "turn/interrupt":
            emit({"id": request_id, "result": {}})
            if active_turn:
                emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": active_turn, "status": "interrupted"}}})
            active_turn = None
        else:
            emit({"id": request_id, "error": {"code": -32601, "message": "Unsupported fixture method"}})


def main():
    args = sys.argv[1:]
    name = Path(sys.argv[0]).name
    if args and args[0] == "--daemon":
        daemon(args[1], args[2])
        return
    if "--version" in args:
        print("2.1.283 (Claude Code)" if name == "claude" else "codex-cli 0.157.1")
    elif "--help" in args:
        print("fixture --bg --resume --session-id --plugin-dir --stdio")
    elif name == "codex" and args[:1] == ["app-server"]:
        app_server()
    elif name == "claude" and args[:1] == ["agents"]:
        emit([json.loads(path.read_text()) for path in ROOT.glob("*.agent.json")])
    elif name == "claude" and args[:1] == ["stop"]:
        candidates = list(ROOT.glob(args[1] + "-*.agent.json"))
        if len(candidates) != 1:
            raise SystemExit("Native stop requires an unambiguous job ID, not session UUID")
        path = candidates[0]
        record("claude_stop", path.name.removesuffix(".agent.json"))
        if path.exists():
            pid = json.loads(path.read_text()).get("pid")
            if pid:
                os.kill(pid, signal.SIGTERM)
    elif name == "claude" and "--bg" in args:
        flag = "--resume" if "--resume" in args else None
        session = args[args.index(flag) + 1] if flag else str(uuid.uuid4())
        options_path = ROOT / (session + ".options.json")
        options = json.loads(options_path.read_text()) if flag and options_path.exists() else {}
        if "--settings" in args:
            options["settings"] = args[args.index("--settings") + 1]
        if flag == "--resume" and ((ROOT / "copy_claude").exists() or any(x in args for x in ("--settings", "--name", "--plugin-dir"))):
            session = str(uuid.uuid4())
        (ROOT / (session + ".options.json")).write_text(json.dumps(options))
        record("claude_start", {"id": session, "resume": flag == "--resume"})
        child_env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_ROOM_")}
        if options.get("settings"):
            child_env.update(json.loads(Path(options["settings"]).read_text()).get("env", {}))
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--daemon", session, os.getcwd()],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=child_env)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            path = ROOT / (session + ".agent.json")
            if path.exists() and json.loads(path.read_text()).get("pid"):
                break
            time.sleep(.02)
        if (ROOT / "hold_claude_launch").exists():
            time.sleep(10)  # Simulate an interrupted launch after its background child exists.
        print("backgrounded · " + session[:8])
    else:
        raise SystemExit("Unsupported fake native invocation")


if __name__ == "__main__":
    main()
