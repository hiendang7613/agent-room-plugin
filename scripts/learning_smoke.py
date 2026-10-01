"""Learning pilot protocol and deterministic checks; no provider execution on import."""

import json
import math
import signal
import time

from agent_room.common import atomic_write
from agent_room.knowledge import Knowledge


PHASES = ("observe", "reuse", "revise")
PREFIX = "LEARNING_RESULT "
CASES = {
    "observe": {"observations": [{"reply": {"retry_after": 1000}, "delay_seconds": 1},
                                  {"reply": {"retry_after": 250}, "delay_seconds": 0.25}]},
    "reuse": {"reply": {"retry_after": 1750}},
    "revise": {"observations": [{"reply": {"retry_after": 3, "retry_after_unit": "seconds"}, "delay_seconds": 3}],
               "reply": {"retry_after": 2, "retry_after_unit": "seconds"}},
}
SCOPE = {
    "scenario": "learning", "script_dispatches": 3, "expected_peer_dispatches": 3,
    "max_persistent_members": 2, "max_room_messages": 6, "restarts": 1,
    "source_edits": "Script-owned fixture JSON only; models may write shared knowledge and peer replies, not source or settings",
    "retries": 0, "model_selection": "Existing native configuration; no override",
    "note": "Guided mechanism pilot, not autonomous-learning or memory-benefit benchmark. Model/tool steps and cost follow native settings; message/time bounds are not token or monetary caps.",
}


class Deadline:
    """Stop on either elapsed clock; a suspended host can clean up only after resume."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.monotonic = time.monotonic()
        self.wall = time.time()

    def remaining(self):
        return self.seconds - max(time.monotonic() - self.monotonic, time.time() - self.wall)

    def check(self):
        if self.remaining() <= 0:
            raise RuntimeError("Learning pilot elapsed-time budget exhausted")

    def observation(self):
        return {"limit_seconds": self.seconds, "monotonic_seconds": time.monotonic() - self.monotonic,
                "wall_seconds": time.time() - self.wall}

    def arm(self):
        self.handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM)}
        signal.signal(signal.SIGALRM, lambda *_: self.check())
        signal.signal(signal.SIGTERM, self.interrupted)
        signal.setitimer(signal.ITIMER_REAL, .5, .5)

    @staticmethod
    def interrupted(signum, frame):
        raise RuntimeError("Learning pilot interrupted; stopping owned processes")

    def disarm(self):
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in self.handlers.items():
            signal.signal(sig, handler)


def prompt(phase):
    instructions = {
        "observe": "Inspect learning-case-observe.json. Infer a useful retry-delay convention from these observed outcomes. Save one advisory knowledge record with its source, conditions and limits; observed samples do not establish every API variant.",
        "reuse": "Inspect learning-case-reuse.json. Use the project's relevant learned convention to propose the retry delay in seconds. Cite the existing knowledge record and current version supporting your answer.",
        "revise": "Inspect learning-case-revise.json. Reconcile the new observation with the previous lesson, then propose the delay in seconds for its reply. Correct or retire the previous lesson using counterevidence; if retiring, save a replacement and cite it. Keep applicability and limits clear.",
    }
    return (f"[Learning pilot phase={phase}] {instructions[phase]} "
            "These are fictional adapter fixtures, not a rule about HTTP Retry-After or other APIs. "
            "This is an authorized isolated analysis/knowledge exercise. Do not edit source/fixtures, create tasks, "
            "spawn agents, change settings or call external services. Use the native tools and Agent Room CLI. "
            "Read case files and room guidance/data only; do not inspect the pilot runner, grader or its report. "
            "ACK this request after processing. Send exactly one substantive result to CLAUDE_01, beginning "
            f"{PREFIX} followed by a JSON object (no code fence) with phase='{phase}', request=THIS_MESSAGE_ID, "
            "knowledge=[{id: actual K-ID, version: actual integer}], and summary explaining your finding and limits. "
            + ("Include delay_seconds as a JSON number. " if phase != "observe" else "")
            + "CLAUDE_01 acknowledges your result without sending a reply. Then finish your turn.")


def result(store, phase, request_id, original=None):
    """Require exact correlated peer processing AND Codex completion, not a model's 'done'."""
    with store.read() as db:
        messages = [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY seq")]
        attempts = [json.loads(row[0]) for row in db.execute("SELECT data FROM attempts ORDER BY rowid")]
    if len(messages) > SCOPE["max_room_messages"]:
        raise RuntimeError("Learning pilot message budget exceeded; no further work is authorized")
    request = next(message for message in messages if message["id"] == request_id)
    if request["status"] != "processed":
        return None
    incoming = [a for a in attempts if a["message"] == request_id]
    if (len(incoming) != 1 or incoming[0]["state"] != "completed"
            or not incoming[0]["processed"] or not incoming[0]["turn_id"]):
        return None
    responses = []
    for message in messages:
        if message["sender"] != "CODEX_EXPERT" or message["recipient"] != "CLAUDE_01" or not message["body"].startswith(PREFIX):
            continue
        try:
            value = json.loads(message["body"][len(PREFIX):])
        except ValueError as exc:
            raise RuntimeError("Malformed learning result; retain it without retrying the model") from exc
        if isinstance(value, dict) and value.get("request") == request_id:
            responses.append((message, value))
    if not responses:
        return None
    if len(responses) != 1:
        raise RuntimeError("Duplicate correlated learning results")
    message, value = responses[0]
    if message["status"] != "processed":
        return None
    outgoing = [a for a in attempts if a["message"] == message["id"]]
    if len(outgoing) != 1 or not outgoing[0]["processed"]:
        return None
    citations = value.get("knowledge")
    if (value.get("phase") != phase or not isinstance(value.get("summary"), str) or not value["summary"].strip()
            or not isinstance(citations, list) or len(citations) != 1 or not isinstance(citations[0], dict)):
        raise RuntimeError("Learning result has invalid phase, explanation or knowledge citation")
    citation = citations[0]
    if not isinstance(citation.get("id"), str) or type(citation.get("version")) is not int:
        raise RuntimeError("Knowledge citation must include actual ID and integer version")
    record = Knowledge(store).show(citation["id"])
    if (record["version"] != citation["version"] or record["state"] != "active"
            or record["author"] != "CODEX_EXPERT" or record["basis"] == "admin"):
        raise RuntimeError("Result cites stale, inactive or incorrectly attributed knowledge")
    if phase == "reuse" and (record["id"] != original["id"] or record["version"] != original["version"]):
        raise RuntimeError("Reuse must cite the record persisted before restart")
    if phase == "revise":
        old = Knowledge(store).show(original["id"])
        if old["version"] <= original["version"] or (record["id"] != old["id"] and old["state"] != "retired"):
            raise RuntimeError("Counterevidence did not revise or retire the original lesson")
    if phase != "observe":
        delay = value.get("delay_seconds")
        expected = 1.75 if phase == "reuse" else 2
        if type(delay) not in {int, float} or not math.isfinite(delay) or not math.isclose(delay, expected, rel_tol=0, abs_tol=1e-9):
            raise RuntimeError(f"Incorrect retry delay in phase {phase}")
    return {"phase": phase, "request": request_id, "peer_message": message["id"], "result": value,
            "knowledge": record, "codex_attempt": incoming[0]["id"], "claude_processing_attempt": outgoing[0]["id"]}


def run(store, command, wait, report):
    original = None
    files = {}
    report["learning"] = {"phases": [], "guided": True, "same_session_memory_confound": True,
                          "sqlite_retrieval_observed": False, "semantic_lesson_review": "pending human/source review",
                          "token_usage": None, "usage_gap": "Current room ledger does not retain complete native provider usage",
                          "claude_turn_completion": "Not exposed by inbox transport; peer processing ACK is checked"}
    for phase in PHASES:
        path = store.project / f"learning-case-{phase}.json"
        content = json.dumps(CASES[phase], indent=2) + "\n"
        atomic_write(path, content)
        files[path] = content
        message = command("send", "--to", "CODEX_EXPERT", text=prompt(phase))
        report["messages"].append({"id": message["id"], "recipient": "CODEX_EXPERT", "phase": phase})
        atomic_write(store.project / "native-smoke-report.json", json.dumps(report, indent=2))
        wait(lambda: result(store, phase, message["id"], original), "learning " + phase + ": result, citation, ACKs and Codex completion")
        observed = result(store, phase, message["id"], original)
        report["learning"]["phases"].append(observed)
        if any(path.read_text() != expected for path, expected in files.items()):
            raise RuntimeError("A read-only learning fixture changed")
        if phase == "observe":
            original = observed["knowledge"]
            native_id = store.member("CODEX_EXPERT")["native_id"]
            command("stop")
            command("start")
            wait(lambda: store.room()["status"] == "running", "learning exact native restart")
            if store.member("CODEX_EXPERT")["native_id"] != native_id:
                raise RuntimeError("Learning pilot resumed a different Codex thread")
            if Knowledge(store).show(original["id"]) != original:
                raise RuntimeError("Learning changed across restart")
            report["learning"]["resumed_native_id"] = native_id
    with store.read() as db:
        messages = [dict(row) for row in db.execute("SELECT id,sender,recipient,status FROM messages")]
        if len(messages) != 6 or any(message["status"] != "processed" for message in messages):
            raise RuntimeError("Learning pilot left extra or unprocessed messages")
        if any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in ("tasks", "notes", "approvals")):
            raise RuntimeError("Learning pilot changed task, decision or approval state")
    report["learning"]["messages"] = messages
