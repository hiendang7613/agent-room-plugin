"""Transactional room state; Markdown files are projections, never a second ledger."""

from collections import Counter
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import json

from agent_room.common import (GATEWAY, MEMBERS, MODES, RoomError, acting_member, canonical_member, atomic_write, dumps,
                               file_lock, fingerprint, native_event_prompt, now, overlaps, scoped_path, uid)
from agent_room.evidence import bounded, capture, digest, matches_terms, nonempty_strings, source_matches
from agent_room.provenance import assess_chain
from agent_room.schema import EXTENSIONS, KNOWLEDGE_SCHEMA, VERSION


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE members (name TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE prompts (id TEXT PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL,
    created TEXT NOT NULL, accounted TEXT, origin TEXT NOT NULL);
CREATE TABLE tasks (id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE claims (task TEXT PRIMARY KEY REFERENCES tasks(id), owner TEXT NOT NULL,
    token TEXT NOT NULL, paths TEXT NOT NULL);
CREATE TABLE notes (id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    sender TEXT NOT NULL, recipient TEXT NOT NULL, task TEXT, body TEXT NOT NULL,
    context TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, detail TEXT);
CREATE TABLE approvals (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    data TEXT NOT NULL, created TEXT NOT NULL);
"""
# DEC-009 (admin, 2026-09-30) and the admin's follow-up answer in the Claude session: a receipt whose host provenance is not
# confirmed as human is refused for these uses. Accounting (bookkeeping) and creating an analysis-only task are recorded
# in prompt.consumed with their provenance but never refused; a host label that marks the prompt non-human refuses every use.
PROTECTED_USES = frozenset({"native_approval", "task_create_implementation", "task_assign", "task_contract",
                            "task_cancel_or_reopen", "note_admin", "knowledge_admin", "message_retry"})
UNPROTECTED_USES = frozenset({"account", "task_create_analysis"})
MAX_MESSAGE_ID_BYTES = 64
TASK_STATES = {"ready", "running", "blocked", "review", "done", "cancelled"}
NOTE_STATES = {
    "question": {"open", "answered", "superseded"},
    "proposal": {"open", "approved", "rejected", "superseded"},
    "decision": {"approved", "superseded"},
}


def validate_fields(data, text=(), lists=()):
    for key in text:
        if key in data and not isinstance(data[key], str):
            raise RoomError(f"{key} must be a string")
    for key in lists:
        if key in data and (not isinstance(data[key], list) or any(not isinstance(x, str) or not x for x in data[key])):
            raise RoomError(f"{key} must be an array of nonempty strings")


class Store:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.space = self.project / "agents_space"
        self.runtime = self.space / ".runtime"
        self.path = self.runtime / "room.sqlite3"
        for path in (self.space, self.runtime, self.path):
            if path.is_symlink():
                raise RoomError(f"Runtime cannot use a symlink: {path}")

    def exists(self):
        return self.path.is_file()

    def connect(self):
        if not self.exists():
            raise RoomError("Room is not initialized. Run /init-agents-space.", "not_initialized")
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def tx(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self.get_room(db)
            yield db
            db.execute("COMMIT")
        except BaseException as error:
            if db.in_transaction:
                # A refused receipt keeps only its own record (source() runs before any other write in these transactions).
                db.execute("COMMIT" if getattr(error, "keep_record", False) else "ROLLBACK")
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            db.execute("BEGIN")
            self.get_room(db)
            yield db
        finally:
            db.close()

    def initialize(self, mode):
        if mode not in MODES:
            raise RoomError("Unknown mode")
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runtime, 0o700)
        with file_lock(self.runtime / "init.lock"):
            if self.exists():
                room = self.room()
                if room["project"] != str(self.project):
                    raise RoomError("Room was moved; automatic adoption is not supported")
                return room
            temporary = self.runtime / ("init-" + uid() + ".sqlite3")
            db = sqlite3.connect(temporary)
            try:
                db.executescript(SCHEMA + EXTENSIONS + KNOWLEDGE_SCHEMA)
                room = {"schema": VERSION, "id": uid("room-"), "project": str(self.project),
                        "mode": mode, "status": "stopped", "manual_stop": False,
                        "owner": None, "supervisor": None, "generation": None,
                        "created": now(), "error": None}
                db.execute("INSERT INTO meta VALUES ('room', ?)", (dumps(room),))
                for name in MEMBERS:
                    db.execute("INSERT INTO members VALUES (?,?)", (name, dumps({
                        "name": name, "native_id": None, "pid": None, "stamp": None,
                        "status": "stopped", "error": None, "turn_id": None,
                    })))
                db.commit()
                db.close()
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                db.close()
                temporary.unlink(missing_ok=True)
            return room

    @staticmethod
    def get_room(db):
        room = json.loads(db.execute("SELECT value FROM meta WHERE key='room'").fetchone()[0])
        if room["schema"] != VERSION:
            raise RoomError("Unsupported room schema. Stop with the compatible plugin, then run agent-room migrate for schema 1 or 2.", "incompatible")
        return room

    @staticmethod
    def put_room(db, room):
        db.execute("UPDATE meta SET value=? WHERE key='room'", (dumps(room),))

    def room(self):
        with self.read() as db:
            room = self.get_room(db)
            if room["project"] != str(self.project):
                raise RoomError("Room belongs to another project location", "conflict")
            return room

    @staticmethod
    def event(db, kind, data):
        return db.execute("INSERT INTO events(kind,data,created) VALUES (?,?,?)", (kind, dumps(data), now())).lastrowid

    def member(self, name, changes=None):
        if name not in MEMBERS:
            raise RoomError("Unknown member")
        with self.tx() as db:
            member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (name,)).fetchone()[0])
            if changes:
                member.update(changes)
                db.execute("UPDATE members SET data=? WHERE name=?", (dumps(member), name))
            return member

    def actor(self):
        name = canonical_member(os.environ.get("AGENT_ROOM_MEMBER", ""))
        session = os.environ.get("AGENT_ROOM_SESSION_ID", "")
        with self.read() as db:
            room = self.get_room(db)
            if name == GATEWAY:
                owner = room.get("owner") or {}
                if owner.get("session") == session and session:
                    return name
            elif name in MEMBERS:
                member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (name,)).fetchone()[0])
                token = os.environ.get("AGENT_ROOM_BINDING", "")
                if token and member.get("token_hash") == hashlib.sha256(token.encode()).hexdigest():
                    if name in MODES[room["mode"]] and room["status"] in {"starting", "running", "stopping"}:
                        return name
        raise RoomError("This process is not bound to an active room member", "identity")

    @staticmethod
    def main_only(actor):
        if actor != GATEWAY:
            raise RoomError("Only CLAUDE_01 records admin intent or changes assignment/authority", "authority")

    @staticmethod
    def source(db, prompt_id, use):
        """Check a receipt before use; only account bookkeeping is outside the protected-use set."""
        if use not in PROTECTED_USES | UNPROTECTED_USES:
            raise RoomError("Unknown receipt use", "invalid")
        row = db.execute("SELECT body,origin FROM prompts WHERE id=?", (prompt_id,)).fetchone() if prompt_id else None
        if not row or row["origin"] == "peer" or native_event_prompt(row["body"]):
            raise RoomError("An original admin prompt ID is required", "authority")
        # The host transcript row exists by now, so the hook-time offset is enough to find it again.
        receipts = [(event[0], json.loads(event[1])) for event in db.execute("SELECT seq, data FROM events WHERE kind='prompt.receipt' ORDER BY seq")]
        mine = next(((seq, data) for seq, data in receipts if data.get("receipt") == prompt_id), None)
        if mine:
            # Earlier receipts with the same text in the same transcript claim their rows first (one row, one receipt).
            offsets = []
            for seq, other in receipts:
                body = db.execute("SELECT body FROM prompts WHERE id=?", (other.get("receipt"),)).fetchone()
                if seq < mine[0] and other.get("transcript") == mine[1].get("transcript") and body and body[0].strip() == row["body"].strip():
                    offsets.append(other.get("offset"))
            provenance = assess_chain(mine[1].get("transcript"), offsets + [mine[1].get("offset")], row["body"])
        else:
            provenance = {"state": "absent", "reason": "no provenance record: receipt predates provenance records or was recovered manually"}
        if provenance["state"] == "non_human":
            message = f"The host transcript labels this prompt {provenance['kind']}, not admin; it cannot be a receipt"
        elif use in PROTECTED_USES and provenance["state"] != "human":
            message = (f"Host provenance of this receipt is {provenance['state']} ({provenance['reason']}); this use needs an admin prompt the "
                       "host confirms as human. Ask the admin to repeat the instruction as plain text; intake account still works")
        else:
            Store.event(db, "prompt.consumed", {"receipt": prompt_id, "use": use} | provenance)
            return provenance
        Store.event(db, "prompt.refused", {"receipt": prompt_id, "use": use} | provenance)
        refusal = RoomError(message, "authority")
        refusal.keep_record = True
        raise refusal

    @staticmethod
    def record(db, table, record_id):
        row = db.execute(f"SELECT version,data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise RoomError(f"Unknown {table} record: {record_id}", "not_found")
        return dict(json.loads(row["data"]), id=record_id, version=row["version"])

    def revision_history(self, table, record_id, after=0, limit=8):
        """Read recorded revisions only; never fabricate history for older records."""
        kind = {"notes": "note.revised", "knowledge": "knowledge.revised"}.get(table)
        if not kind or after < 0 or not 1 <= limit <= 50:
            raise RoomError("Use notes/knowledge history, after >= 0 and limit 1..50")
        with self.read() as db:
            current = self.record(db, table, record_id)
            items = []
            for row in db.execute("SELECT seq,data FROM events WHERE kind=? AND seq>? ORDER BY seq", (kind, after)):
                record = json.loads(row["data"])
                if record["id"] == record_id:
                    items.append(dict(record, cursor=row["seq"]))
                if len(items) > limit:
                    break
        return {"current_version": current["version"], "current_state": current["state"], "historical": True,
                "items": items[:limit], "next_after": items[limit - 1]["cursor"] if len(items) > limit else None}

    @staticmethod
    def save(db, table, record, expected):
        if record["version"] != expected:
            raise RoomError("Record changed. Read current state and reconcile before retrying.",
                            "conflict", current_version=record["version"])
        record["version"] += 1
        record["updated"] = now()
        db.execute(f"UPDATE {table} SET version=?,data=? WHERE id=?",
                   (record["version"], dumps(record), record["id"]))

    def require_ready(self, db, task):
        for dependency in task["dependencies"]:
            if self.record(db, "tasks", dependency)["state"] != "done":
                raise RoomError(f"Dependency is not complete: {dependency}", "dependency")
        for note_id, revision in task.get("decisions", {}).items():
            note = self.record(db, "notes", note_id)
            if note["version"] != revision or note["state"] != "approved":
                raise RoomError("Reconcile changed decisions first", "conflict")
            if note.get("condition") and not note.get("condition_evidence"):
                raise RoomError("Approval condition has not been satisfied", "authority")

    def intake(self, session, body, receipt=None, origin="hook", provenance=None):
        receipt = receipt or uid("P-")
        with self.tx() as db:
            existing = db.execute("SELECT * FROM prompts WHERE id=?", (receipt,)).fetchone()
            if existing:
                if existing["session"] != session or existing["body"] != body:
                    raise RoomError("Receipt ID reused with different prompt", "conflict")
            else:
                db.execute("INSERT INTO prompts VALUES (?,?,?,?,NULL,?)", (receipt, session, body, now(), origin))
                if provenance:
                    self.event(db, "prompt.receipt", {"receipt": receipt, "session": session} | provenance)
        return receipt

    def account(self, actor, prompt_id, disposition, refs):
        self.main_only(actor)
        if not disposition.strip():
            raise RoomError("Record how each intent was handled; a status question need not create a task")
        with self.tx() as db:
            self.source(db, prompt_id, "account")
            for ref in refs:
                if not any(db.execute(f"SELECT 1 FROM {table} WHERE id=?", (ref,)).fetchone()
                           for table in ("tasks", "notes", "knowledge")):
                    raise RoomError(f"Unknown disposition reference: {ref}")
            db.execute("UPDATE prompts SET accounted=? WHERE id=?",
                       (dumps({"disposition": disposition, "refs": refs}), prompt_id))

    def create_task(self, actor, data):
        self.main_only(actor)
        if data.keys() - {"title", "request", "acceptance", "next", "owner", "source", "authority", "scope", "dependencies", "review_policy", "reviewer"}:
            raise RoomError("Unsupported task creation fields")
        validate_fields(data, ("title", "request", "acceptance", "next", "owner", "source", "authority"), ("scope", "dependencies"))
        for key in ("title", "request", "acceptance", "next", "owner", "source"):
            if not data.get(key):
                raise RoomError(f"Task requires {key}")
        authority = data.get("authority", "analysis")
        if authority not in {"analysis", "implementation"}:
            raise RoomError("authority must be analysis or implementation")
        paths = [scoped_path(self.project, path) for path in data.get("scope", [])]
        with self.tx() as db:
            self.source(db, data["source"], "task_create_implementation" if authority == "implementation" else "task_create_analysis")
            room = self.get_room(db)
            if data["owner"] not in MODES[room["mode"]]:
                raise RoomError("Task owner must be active in the room mode")
            policy, reviewer = data.get("review_policy", "none"), data.get("reviewer")
            self.validate_review_policy(room, data["owner"], policy, reviewer)
            dependencies = data.get("dependencies", [])
            for dependency in dependencies:
                self.record(db, "tasks", dependency)
            task = dict(data, id=uid("T-"), version=1, authority=authority, scope=paths,
                        dependencies=dependencies, state="ready", checkpoint="", evidence=[],
                        created=now(), updated=now(), decisions={}, review_policy=policy, reviewer=reviewer,
                        submission=None, checkpoint_id=None, contract_revision=1, last_progress=now())
            db.execute("INSERT INTO tasks VALUES (?,?,?)", (task["id"], 1, dumps(task)))
            self.event(db, "task.created", {"id": task["id"], "actor": actor})
            if task["owner"] != actor:
                self.notify(db, actor, task["owner"], "New assigned task. Read the task and current decisions before acting.", task["id"])
        return task

    def update_task(self, actor, task_id, expected, changes):
        validate_fields(changes, ("state", "checkpoint", "next", "blocked_reason", "owner", "authority", "acceptance", "request", "source"), ("evidence", "scope"))
        if "snapshot" in changes:
            snapshot = changes["snapshot"]
            if not isinstance(snapshot, dict) or any(not isinstance(k, str) or (v is not None and not isinstance(v, str)) for k, v in snapshot.items()):
                raise RoomError("snapshot must map relative file names to hashes or null")
        allowed = {"state", "checkpoint", "next", "evidence", "snapshot", "blocked_reason"}
        admin_fields = {"owner", "scope", "authority", "acceptance", "request", "source", "review_policy", "reviewer"}
        if changes.keys() - allowed - admin_fields:
            raise RoomError("Unsupported task update fields")
        with self.tx() as db:
            task = self.record(db, "tasks", task_id)
            if actor != task["owner"] and actor != GATEWAY:
                raise RoomError("Only the task owner or CLAUDE_01 can update it", "authority")
            state = changes.get("state", task["state"])
            previous_state = task["state"]
            if state not in TASK_STATES:
                raise RoomError("Invalid task state")
            terminal_transition = ((state == "cancelled" and previous_state != "cancelled")
                                   or (previous_state in {"done", "cancelled"} and state != previous_state))
            if terminal_transition:
                self.main_only(actor)
                self.source(db, changes.get("source"), "task_cancel_or_reopen")
            contract_fields = changes.keys() & (admin_fields - {"source"})
            if contract_fields or ("source" in changes and not terminal_transition):
                self.main_only(actor)
                self.source(db, changes.get("source"), "task_assign" if contract_fields & {"owner", "scope", "authority"} else "task_contract")
                if changes.keys() & {"owner", "scope", "authority"} and db.execute("SELECT 1 FROM claims WHERE task=?", (task_id,)).fetchone():
                    raise RoomError("Release the writer claim before changing assignment/scope", "conflict")
            if "owner" in changes and changes["owner"] not in MODES[self.get_room(db)["mode"]]:
                raise RoomError("Owner is inactive")
            if "scope" in changes:
                changes = dict(changes, scope=[scoped_path(self.project, x) for x in changes["scope"]])
            if changes.get("authority", task["authority"]) not in {"analysis", "implementation"}:
                raise RoomError("Invalid authority")
            self.validate_review_policy(self.get_room(db), changes.get("owner", task["owner"]),
                                        changes.get("review_policy", task["review_policy"]), changes.get("reviewer", task["reviewer"]))
            contract_changed = any(changes[key] != task.get(key) for key in changes.keys() & admin_fields)
            if contract_changed:
                task["contract_revision"] += 1
            if state in {"running", "review", "done"}:
                self.require_ready(db, task)
            if state == "done":
                if not changes.get("evidence", task["evidence"]):
                    raise RoomError("Completion requires acceptance evidence")
                snapshot = changes.get("snapshot", task.get("snapshot", {}))
                if snapshot and fingerprint(self.project, snapshot) != snapshot:
                    raise RoomError("Reviewed source changed; reconcile before completion", "conflict")
                if changes.get("review_policy", task["review_policy"]) == "peer_required":
                    self.main_only(actor)
                    if self.review_status(db, dict(task, **changes))["state"] != "approved":
                        raise RoomError("Completion needs an approved peer receipt for the current submission/source/contract", "review_required")
            task.update(changes)
            if any(key in changes for key in {"checkpoint", "evidence", "state"}):
                task["last_progress"] = now()
            self.save(db, "tasks", task, expected)
            if state in {"done", "cancelled", "blocked", "review"} and actor == task["owner"]:
                db.execute("DELETE FROM claims WHERE task=?", (task_id,))
            if changes.keys() & admin_fields and task["owner"] != actor:
                self.notify(db, actor, task["owner"], "Task assignment or authority changed. Read its current revision.", task_id)
            if state == "done" and previous_state != "done":
                for row in db.execute("SELECT data FROM tasks").fetchall():
                    dependent = json.loads(row[0])
                    if task_id in dependent["dependencies"] and dependent["state"] not in {"done", "cancelled"}:
                        self.notify(db, actor, dependent["owner"], f"Dependency {task_id} completed. Reconcile all remaining dependencies and blockers before continuing.", dependent["id"])
            self.event(db, "task.updated", {"id": task_id, "version": task["version"], "actor": actor})
        return task

    def claim(self, actor, task_id, expected):
        with self.tx() as db:
            task = self.record(db, "tasks", task_id)
            if actor != task["owner"] or task["authority"] != "implementation" or not task["scope"]:
                raise RoomError("A writer needs an assigned implementation task with explicit scope", "authority")
            if task["version"] != expected or task["state"] not in {"ready", "running"}:
                raise RoomError("Read the current actionable task before claiming", "conflict")
            self.require_ready(db, task)
            for row in db.execute("SELECT * FROM claims"):
                if row["task"] == task_id:
                    return dict(row, paths=json.loads(row["paths"]))
                if any(overlaps(a, b) for a in task["scope"] for b in json.loads(row["paths"])):
                    raise RoomError("Another task owns an overlapping path", "conflict", task=row["task"])
            token = uid()
            db.execute("INSERT INTO claims VALUES (?,?,?,?)", (task_id, actor, token, dumps(task["scope"])))
            task["state"] = "running"
            task["last_progress"] = now()
            self.save(db, "tasks", task, expected)
            self.event(db, "writer.claimed", {"task": task_id, "owner": actor})
            return {"task": task_id, "owner": actor, "token": token, "paths": task["scope"], "version": task["version"]}

    def release(self, actor, task_id, token):
        with self.tx() as db:
            row = db.execute("SELECT * FROM claims WHERE task=?", (task_id,)).fetchone()
            if not row:
                return {"released": False}
            if row["owner"] != actor or row["token"] != token:
                raise RoomError("Only the claimant with its token may release this scope", "authority")
            db.execute("DELETE FROM claims WHERE task=?", (task_id,))
            return {"released": True}

    @staticmethod
    def validate_review_policy(room, owner, policy, reviewer):
        if policy not in ("none", "peer_required"):
            raise RoomError("review_policy must be none or peer_required")
        if policy == "peer_required" and (reviewer not in MODES[room["mode"]] or reviewer == owner):
            raise RoomError("peer_required needs an active reviewer different from the task owner")
        if policy == "none" and reviewer is not None:
            raise RoomError("Set reviewer to null when review_policy is none")

    @staticmethod
    def entry(db, table, record_id):
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise RoomError(f"Unknown {table} record: {record_id}", "not_found")
        return json.loads(row[0])

    def review_status(self, db, task):
        if task["review_policy"] == "none":
            return {"state": "not_required"}
        if not task.get("submission"):
            return {"state": "not_submitted"}
        submission = self.entry(db, "submissions", task["submission"])
        result = {"submission": submission["id"], "source_digest": submission["digest"]}
        if (submission["contract_revision"] != task["contract_revision"] or submission["author"] != task["owner"]
                or submission["reviewer"] != task["reviewer"] or submission["decisions"] != task["decisions"]):
            return dict(result, state="stale", reason="task contract/owner/decisions changed")
        if submission["evidence"] != task["evidence"]:
            return dict(result, state="stale", reason="submission evidence changed")
        if any(self.record(db, "tasks", key)["version"] != version for key, version in submission["dependencies"].items()):
            return dict(result, state="stale", reason="dependency revision changed")
        if not source_matches(self.project, submission["snapshot"]):
            return dict(result, state="stale", reason="source changed or cannot be read")
        receipts = [json.loads(r[0]) for r in db.execute("SELECT data FROM reviews WHERE submission=? ORDER BY rowid", (submission["id"],))]
        if not receipts:
            return dict(result, state="pending", reviewer=submission["reviewer"])
        receipt = receipts[-1]
        return dict(result, state="approved" if receipt["verdict"] == "approve" else receipt["verdict"], receipt=receipt["id"])

    def submit_task(self, actor, task_id, expected, data):
        if data.keys() - {"paths", "evidence", "summary"}:
            raise RoomError("Submission accepts only paths, evidence and summary; the store owns source hashes")
        nonempty_strings(data.get("evidence"), "evidence")
        if not isinstance(data.get("summary"), str) or not data["summary"].strip() or len(data["summary"]) > 4000:
            raise RoomError("Submission requires a nonempty summary of at most 4000 characters")
        with self.tx() as db:
            task = self.record(db, "tasks", task_id)
            if actor != task["owner"]:
                raise RoomError("Only the author/assigned owner can submit their work", "authority")
            if task["version"] != expected or task["state"] not in {"ready", "running", "review", "blocked"}:
                raise RoomError("Read the current unfinished task before submitting", "conflict")
            self.require_ready(db, task)
            snapshot = capture(self.project, data.get("paths"))
            if not any(value is not None for value in snapshot.values()):
                raise RoomError("Submission needs at least one readable source/report file; include a report for deletion-only work")
            submission = {"id": uid("S-"), "task": task_id, "task_version": task["version"],
                          "contract_revision": task["contract_revision"], "author": actor, "reviewer": task["reviewer"],
                          "snapshot": snapshot, "decisions": task["decisions"], "evidence": data["evidence"],
                          "summary": data["summary"], "created": now()}
            submission["previous_submission"] = task["submission"]
            submission["dependencies"] = {key: self.record(db, "tasks", key)["version"] for key in task["dependencies"]}
            submission["attempts"] = [row[0] for row in db.execute("SELECT id FROM attempts WHERE task=? AND member=? ORDER BY rowid DESC LIMIT 20", (task_id, actor))]
            submission["digest"] = digest(submission)
            db.execute("INSERT INTO submissions VALUES (?,?,?)", (submission["id"], task_id, dumps(submission)))
            task.update(submission=submission["id"], state="review", evidence=data["evidence"], last_progress=now())
            self.save(db, "tasks", task, expected)
            db.execute("DELETE FROM claims WHERE task=? AND owner=?", (task_id, actor))
            self.event(db, "task.submitted", {"task": task_id, "submission": submission["id"], "actor": actor})
            if task["reviewer"]:
                self.notify(db, actor, task["reviewer"], f"Review submission {submission['id']}. Read its source digest and current task context; record a review receipt after checking the listed files and acceptance evidence.", task_id)
            return {"task": task, "submission": submission}

    def record_review(self, actor, submission_id, data):
        if data.keys() - {"source_digest", "verdict", "summary", "findings", "evidence"}:
            raise RoomError("Unsupported review fields")
        nonempty_strings(data.get("evidence"), "review evidence")
        verdict, findings = data.get("verdict"), data.get("findings")
        if verdict not in ("approve", "changes_requested", "blocked"):
            raise RoomError("verdict must be approve, changes_requested or blocked")
        if not isinstance(data.get("summary"), str) or not data["summary"].strip() or len(data["summary"]) > 4000:
            raise RoomError("Review requires a nonempty summary of at most 4000 characters")
        if not isinstance(findings, list) or len(findings) > 100:
            raise RoomError("findings must be an array of at most 100 findings")
        if (verdict == "approve" and findings) or (verdict == "changes_requested" and not findings):
            raise RoomError("Approve needs no unresolved findings; changes_requested needs findings")
        for finding in findings:
            if not isinstance(finding, dict) or finding.keys() - {"summary", "severity", "path", "line"}:
                raise RoomError("Finding accepts summary, severity and optional path/line")
            if not isinstance(finding.get("summary"), str) or not finding["summary"].strip() or len(finding["summary"]) > 4000:
                raise RoomError("Finding requires a bounded nonempty summary")
            if finding.get("severity") not in ("high", "medium", "low"):
                raise RoomError("Finding severity must be high, medium or low")
            if "line" in finding and (type(finding["line"]) is not int or finding["line"] < 1 or "path" not in finding):
                raise RoomError("Finding line requires a path and positive integer")
        with self.tx() as db:
            submission = self.entry(db, "submissions", submission_id)
            task = self.record(db, "tasks", submission["task"])
            if actor == submission["author"] or actor != task["reviewer"] or actor not in MODES[self.get_room(db)["mode"]]:
                raise RoomError("Only the assigned active peer reviewer can record this receipt", "authority")
            if data.get("source_digest") != submission["digest"]:
                raise RoomError("Read and inspect the exact submission digest before recording review", "conflict")
            if task["submission"] != submission_id or task["state"] != "review" or self.review_status(db, task)["state"] == "stale":
                raise RoomError("Submission is stale or task is no longer in review; reconcile and resubmit", "conflict")
            for finding in findings:
                if "path" in finding and (not isinstance(finding["path"], str) or finding["path"] not in submission["snapshot"]):
                    raise RoomError("Finding path must be one of the submitted files")
            old = db.execute("SELECT data FROM reviews WHERE submission=?", (submission_id,)).fetchone()
            if old:
                previous = json.loads(old[0])
                if all(previous.get(key) == data.get(key) for key in ("source_digest", "verdict", "summary", "findings", "evidence")):
                    return previous
                raise RoomError("Review receipts are immutable; author must resubmit after reconciling findings", "conflict")
            receipt = dict(data, id=uid("R-"), submission=submission_id, task=task["id"], reviewer=actor, created=now())
            db.execute("INSERT INTO reviews VALUES (?,?,?)", (receipt["id"], submission_id, dumps(receipt)))
            task["last_progress"] = now()
            self.save(db, "tasks", task, task["version"])
            self.event(db, "review.recorded", {"task": task["id"], "review": receipt["id"], "verdict": verdict, "actor": actor})
            for recipient in {task["owner"], GATEWAY} - {actor}:
                self.notify(db, actor, recipient, f"Review {receipt['id']}: {verdict}. Read findings and current source before the next action; task completion remains separate.", task["id"])
            return receipt

    def checkpoint(self, actor, task_id, expected, data):
        if data.keys() - {"summary", "last_safe_action", "unknown_effects", "next", "paths"}:
            raise RoomError("Unsupported checkpoint fields")
        for key in ("summary", "last_safe_action", "next"):
            if not isinstance(data.get(key), str) or not data[key].strip() or len(data[key]) > 4000:
                raise RoomError(f"Checkpoint requires {key}, at most 4000 characters")
        unknown = data.get("unknown_effects", [])
        if unknown:
            nonempty_strings(unknown, "unknown_effects")
        elif not isinstance(unknown, list):
            raise RoomError("unknown_effects must be an array")
        with self.tx() as db:
            task = self.record(db, "tasks", task_id)
            if actor != task["owner"] or task["state"] in {"done", "cancelled"}:
                raise RoomError("Only the current owner can checkpoint unfinished work", "authority")
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM checkpoints WHERE task=?", (task_id,)).fetchone()[0]
            member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (actor,)).fetchone()[0])
            checkpoint = dict(data, id=uid("C-"), task=task_id, sequence=sequence, owner=actor,
                              task_version=task["version"] + 1, contract_revision=task["contract_revision"],
                              generation=self.get_room(db)["generation"], native_id=member["native_id"],
                              decisions=task["decisions"], snapshot=capture(self.project, data.get("paths")),
                              unknown_effects=unknown, created=now())
            checkpoint["digest"] = digest(checkpoint)
            task.update(checkpoint=data["summary"], checkpoint_id=checkpoint["id"], next=data["next"], last_progress=now())
            self.save(db, "tasks", task, expected)
            db.execute("INSERT INTO checkpoints VALUES (?,?,?,?)", (checkpoint["id"], task_id, sequence, dumps(checkpoint)))
            self.event(db, "task.checkpoint", {"task": task_id, "checkpoint": checkpoint["id"], "sequence": sequence})
            return checkpoint

    def task_context(self, task_id, compact=False):
        with self.read() as db:
            task = self.record(db, "tasks", task_id)
            review = self.review_status(db, task)
            checkpoint = self.entry(db, "checkpoints", task["checkpoint_id"]) if task.get("checkpoint_id") else None
            reasons = []
            if checkpoint:
                if checkpoint["owner"] != task["owner"]:
                    reasons.append("owner changed; handoff required")
                if checkpoint["summary"] != task["checkpoint"]:
                    reasons.append("legacy checkpoint text changed; reconcile with structured checkpoint")
                if checkpoint["contract_revision"] != task["contract_revision"] or checkpoint["decisions"] != task["decisions"]:
                    reasons.append("task contract/decisions changed")
                if checkpoint["generation"] != self.get_room(db)["generation"]:
                    reasons.append("native generation changed; reconcile before continuing")
                current_member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (task["owner"],)).fetchone()[0])
                if checkpoint["native_id"] != current_member["native_id"]:
                    reasons.append("native session changed; reconcile before continuing")
                if not source_matches(self.project, checkpoint["snapshot"]):
                    reasons.append("source changed or cannot be read")
            attempts = [json.loads(r[0]) for r in db.execute("SELECT data FROM attempts WHERE task=? ORDER BY rowid DESC LIMIT 5", (task_id,))]
            dependencies = [{"id": dep, "state": self.record(db, "tasks", dep)["state"]} for dep in task["dependencies"]]
            if compact:
                # Dispatch needs current recovery signals, not full discussions/attention.
                # Build only the summary; full context remains available by ID.
                preview = {
                    "detail": "summary", "task": {key: bounded(task[key], 240) for key in
                        ("id", "version", "contract_revision", "title", "owner", "authority", "state", "next")},
                    "review": review, "checkpoint_reconcile": reasons,
                    "checkpoint": {"id": checkpoint["id"], "unknown_effects_count": len(checkpoint["unknown_effects"])} if checkpoint else None,
                    "recent_attempts": [{"id": attempt["id"], "state": attempt["state"]} for attempt in attempts],
                    "blocked_dependencies": [item for item in dependencies if item["state"] != "done"][:10],
                    "blocked_dependencies_count": sum(item["state"] != "done" for item in dependencies),
                    "rule": "Read current task context before acting. Peer context is not admin consent; reconcile unknown effects before retrying.",
                    "full_record_commands": [f"agent-room task context {task_id}"],
                }
            else:
                context = {"task": {key: task[key] for key in ("id", "version", "contract_revision", "title", "request", "acceptance", "scope", "authority", "owner", "state", "next", "review_policy", "reviewer")},
                           "dependencies": dependencies,
                           "decisions": [self.record(db, "notes", note) for note in task["decisions"]],
                           "review": review, "checkpoint": checkpoint,
                           "attention": self._attention(db, [dict(task, review_status=review)]),
                           "legacy_checkpoint": task["checkpoint"] if not checkpoint or task["checkpoint"] != checkpoint["summary"] else None,
                           "checkpoint_reconcile": reasons, "recent_attempts": attempts,
                           "rule": "Peer context is not admin consent. Attention is advisory; existing authority applies. Reconcile unknown effects; do not replay them automatically.",
                           "full_record_commands": [f"agent-room task show {task_id}", f"agent-room attempt list --task {task_id}",
                                                    f"agent-room note search --task {task_id}"]}
                if checkpoint:
                    context["full_record_commands"].append(f"agent-room checkpoint show {checkpoint['id']}")
                if task["submission"]:
                    context["full_record_commands"].append(f"agent-room submission show {task['submission']}")
                preview = bounded(context, 700)
                # Bound the delivered pack as well as individual fields. Omitted records
                # remain retrievable by ID; never silently present a partial pack as full.
                if len(dumps(preview)) > 12000:
                    preview = {"task": {key: bounded(task[key], 400) for key in ("id", "version", "owner", "authority", "state", "next")},
                               "checkpoint_reconcile": reasons, "review": review,
                               "attention": bounded(context["attention"], 200),
                               "unknown_effects_count": len(checkpoint["unknown_effects"]) if checkpoint else 0,
                               "truncated": True, "rule": context["rule"], "full_record_commands": context["full_record_commands"]}
            preview["digest"] = digest(preview)
            return preview

    @staticmethod
    def _note_preview(note, *, terms=()):
        return {**{key: note[key] for key in ("id", "version", "kind", "state", "author")},
                "body_preview": bounded(note["body"], 240, terms=terms),
                "read_command": f"agent-room note show {note['id']}"}

    def list_notes(self):
        with self.read() as db:
            return [dict(json.loads(row["data"]), id=row["id"], version=row["version"])
                    for row in db.execute("SELECT id,version,data FROM notes ORDER BY rowid")]

    def search_notes(self, query="", *, state="open", author=None, kind=None, task_id=None, after=0, limit=8):
        if after < 0 or not 1 <= limit <= 50:
            raise RoomError("Use after >= 0 and limit 1..50")
        if not isinstance(query, str) or len(query) > 200:
            raise RoomError("Search query must be text, at most 200 characters")
        if state != "all" and not any(state in states for states in NOTE_STATES.values()):
            raise RoomError("Use an existing note state or all")
        if author is not None and author not in MEMBERS:
            raise RoomError("Unknown note author")
        if kind is not None and kind not in NOTE_STATES:
            raise RoomError("Use kind question/proposal/decision")
        terms, items = query.casefold().split(), []
        with self.read() as db:
            if task_id is not None:
                self.record(db, "tasks", task_id)
            for row in db.execute("SELECT rowid AS cursor,id,version,data FROM notes WHERE rowid>? ORDER BY rowid", (after,)):
                note = dict(json.loads(row["data"]), id=row["id"], version=row["version"])
                if ((state != "all" and note["state"] != state) or (author is not None and note["author"] != author)
                        or (kind is not None and note["kind"] != kind)
                        or (task_id is not None and task_id not in note["tasks"])):
                    continue
                if terms and not matches_terms(" ".join(note.get(key, "") for key in ("body", "answer", "condition", "condition_evidence")), terms):
                    continue
                items.append(dict(self._note_preview(note, terms=terms), answer_preview=bounded(note.get("answer", ""), 240, terms=terms), cursor=row["cursor"]))
                if len(items) > limit:
                    break
        return {"items": items[:limit], "next_after": items[limit - 1]["cursor"] if len(items) > limit else None,
                "filters": {"query": query, "state": state, "author": author, "kind": kind, "task": task_id},
                "matching": "All whitespace-separated terms, literal case-insensitive substring in current body, answer and conditions; insertion order.",
                "rule": "Discussion recall is advisory, not a task or permission. Read note show before acting. Keep filters while paging; start each fresh search at after=0 to find revised or reopened notes."}

    def add_note(self, actor, data):
        if actor not in MEMBERS:
            raise RoomError("Unknown member", "identity")
        if "resolution" in data:
            raise RoomError("The room records resolution provenance; do not supply resolution metadata")
        validate_fields(data, ("kind", "body", "state", "condition", "condition_evidence", "source"), ("tasks",))
        kind = data.get("kind")
        if kind not in NOTE_STATES or not data.get("body"):
            raise RoomError("Note requires kind question/proposal/decision and body")
        state = data.get("state", "approved" if kind == "decision" else "open")
        if state not in NOTE_STATES[kind]:
            raise RoomError("Invalid note state")
        with self.tx() as db:
            if kind == "decision" or state != "open" or data.get("source"):
                self.main_only(actor)
                self.source(db, data.get("source"), "note_admin")
            tasks = data.get("tasks", [])
            for task in tasks:
                self.record(db, "tasks", task)
            note = dict(data, id=uid({"question": "Q-", "proposal": "N-", "decision": "D-"}[kind]),
                        version=1, kind=kind, state=state, tasks=tasks, author=actor, created=now(), updated=now())
            db.execute("INSERT INTO notes VALUES (?,?,?)", (note["id"], 1, dumps(note)))
            self._note_changed(db, actor, note)
            self.event(db, "note.revised", note)
            return note

    def resolve_note(self, actor, note_id, expected, data):
        validate_fields(data, ("state", "answer", "source", "condition_evidence", "superseded_by"))
        if data.keys() - {"state", "answer", "source", "condition_evidence", "superseded_by"}:
            raise RoomError("Unsupported note resolution fields")
        with self.tx() as db:
            note = self.record(db, "notes", note_id)
            state = data.get("state", note["state"])
            if state not in NOTE_STATES[note["kind"]]:
                raise RoomError("Invalid resolution state")
            # Author/main may follow up on an ordinary idea. Any admin provenance,
            # approval or existing legacy task binding keeps the admin receipt boundary.
            bound = any(note_id in self.record(db, "tasks", task)["decisions"] for task in note["tasks"])
            admin = (note["kind"] == "decision" or "approved" in {state, note["state"]}
                     or bool(note.get("source")) or bound or "source" in data or "condition_evidence" in data)
            if admin:
                self.main_only(actor)
                self.source(db, data.get("source"), "note_admin")
            elif actor not in MEMBERS or actor not in {note["author"], GATEWAY}:
                raise RoomError("Only the author or main may resolve this advisory note; send counterevidence to its author", "authority")
            if not data.get("answer", "").strip():
                raise RoomError("Keep the answer or reason and its scope")
            if data.get("superseded_by"):
                if state != "superseded":
                    raise RoomError("Use state=superseded when naming a replacement note")
                if data["superseded_by"] == note_id:
                    raise RoomError("A note cannot supersede itself")
                self.record(db, "notes", data["superseded_by"])
            note.update(data)
            if state != "superseded":
                note.pop("superseded_by", None)
            note["resolution"] = {"actor": actor, "basis": "admin" if admin else "peer"}
            self.save(db, "notes", note, expected)
            self._note_changed(db, actor, note)
            self.event(db, "note.revised", note)
            return note

    def _note_changed(self, db, actor, note):
        notified = set()
        summary = f"{note['kind']} {note['id']} v{note['version']} is {note['state']}. "
        for task_id in dict.fromkeys(note["tasks"]):
            task = self.record(db, "tasks", task_id)
            # An idea is advisory until main explicitly approves it. Preserve already
            # bound notes (including <=0.2.1 proposals) until an explicit resolution.
            binding = note["kind"] != "question" and (note["state"] == "approved" or note["id"] in task["decisions"])
            if binding:
                task["contract_revision"] += 1
                task["decisions"][note["id"]] = note["version"]
                if note["state"] == "superseded":
                    task["decisions"].pop(note["id"], None)
                self.save(db, "tasks", task, task["version"])
            if task["owner"] != actor:
                instruction = ("Read current decisions and reconcile affected work." if binding else
                               "Read the advisory note; the task contract and authority are unchanged.")
                self.notify(db, actor, task["owner"], summary + instruction, task_id)
                notified.add(task["owner"])
        for recipient in {GATEWAY, note["author"]} - notified - {actor}:
            self.notify(db, actor, recipient, summary + f"Read agent-room note show {note['id']} before acting; this notice grants no authority.")

    def wake_resumed_work(self, generation):
        """Queue current obligations, never replay a previous native attempt."""
        with self.tx() as db:
            room = self.get_room(db)
            if room["generation"] != generation or room["status"] != "starting":
                return
            for row in db.execute("SELECT data FROM tasks").fetchall():
                task = json.loads(row[0])
                recipients = {}
                if task["owner"] != GATEWAY and task["state"] in {"ready", "running", "review"}:
                    recipients[task["owner"]] = "Reconcile your checkpoint, current source and decisions before continuing authorized unfinished work."
                if task["state"] == "review" and task.get("reviewer") in MODES[room["mode"]]:
                    review = self.review_status(db, task)
                    if review["state"] == "pending":
                        recipients[task["reviewer"]] = (f"Your review of submission {review['submission']} is still pending. "
                            "Read the current task context, submission and source before recording a review receipt.")
                for member, body in recipients.items():
                    if member not in MODES[room["mode"]]:
                        continue
                    # A queued event with current task context will already wake this member.
                    # Old-submission or unrelated room messages do not satisfy this obligation.
                    queued = db.execute("SELECT context FROM messages WHERE recipient=? AND task=? AND status='queued'",
                                        (member, task["id"])).fetchall()
                    if any(json.loads(message["context"]).get("task_version") == task["version"] for message in queued):
                        continue
                    self.notify(db, GATEWAY, member,
                               "Room resumed. " + body + " Do not replay effects of unknown outcome.", task["id"],
                               broadcast=False)

    def queue(self, db, sender, recipient, body, task_id=None, message_id=None, *, knowledge_id=None, kind=None,
              admin_relay=False, broadcast_id=None, broadcast_recipient=None):
        if sender not in MEMBERS or recipient not in MEMBERS or not isinstance(body, str) or not body.strip():
            raise RoomError("Message requires a known recipient and nonempty body")
        if len(body) > 16000:
            raise RoomError("Keep messages under 16000 characters; link longer findings")
        message_id = message_id or uid("M-")
        if not isinstance(message_id, str):
            raise RoomError("Message ID must be text")
        expected_kind = "task" if task_id else "peer"
        kind = expected_kind if kind is None else kind
        if kind not in {"peer", "task", "system"} or (kind != "system" and kind != expected_kind):
            raise RoomError("Message kind must match peer/task context or be system")
        old = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if old:
            old_context = json.loads(old["context"])
            old_knowledge = old_context.get("knowledge", {}).get("id")
            old_kind = old_context.get("kind", "task" if old["task"] else "peer")
            old_admin_relay = old_context.get("admin_relay", False)
            old_broadcast = old_context.get("broadcast", {})
            wanted_broadcast = ({"id": broadcast_id, "direct_recipient": broadcast_recipient}
                                if broadcast_id else {})
            if (old["sender"], old["recipient"], old["task"], old["body"], old_knowledge, old_kind,
                old_admin_relay, old_broadcast) != (sender, recipient, task_id, body, knowledge_id, kind,
                                                     admin_relay, wanted_broadcast):
                raise RoomError("Message ID reused with different content", "conflict")
            return dict(old)
        try:
            message_id_bytes = message_id.encode("ascii")
        except UnicodeEncodeError as exc:
            raise RoomError(f"Message ID must be an ASCII token of at most {MAX_MESSAGE_ID_BYTES} bytes") from exc
        if (not message_id_bytes or len(message_id_bytes) > MAX_MESSAGE_ID_BYTES or
                not all(char.isalnum() or char in "._-" for char in message_id)):
            raise RoomError(f"Message ID must use letters, digits, dot, underscore or hyphen (max {MAX_MESSAGE_ID_BYTES} bytes)")
        context = {}
        if task_id:
            task = self.record(db, "tasks", task_id)
            context = {"task_version": task["version"], "decisions": task["decisions"]}
        if knowledge_id is not None:
            knowledge = self.record(db, "knowledge", knowledge_id)
            context["knowledge"] = {"id": knowledge["id"], "version": knowledge["version"]}
        if admin_relay:
            context["admin_relay"] = True
        if broadcast_id:
            if broadcast_recipient not in MEMBERS:
                raise RoomError("Broadcast copy requires a direct room recipient")
            context["broadcast"] = {"id": broadcast_id, "direct_recipient": broadcast_recipient}
        if kind == "system":
            context["kind"] = kind
        db.execute("INSERT INTO messages(id,sender,recipient,task,body,context,status,created) VALUES (?,?,?,?,?,?,?,?)",
                   (message_id, sender, recipient, task_id, body, dumps(context), "queued", now()))
        return dict(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def fanout(self, db, message, sender, recipient, body, task_id=None, knowledge_id=None, kind="peer"):
        """Queue the same member message for every other room member in the caller's transaction."""
        targets = [name for name in MEMBERS if name != sender]
        for target in targets:
            if target != recipient:
                self.queue(db, sender, target, body, task_id, knowledge_id=knowledge_id, kind=kind,
                           broadcast_id=message["id"], broadcast_recipient=recipient)
        self.event(db, "message.broadcast", {"message": message["id"], "sender": sender, "members": targets})
        return targets

    def notify(self, db, sender, recipient, body, task_id=None, *, broadcast=True):
        """Queue a member-triggered room notice, fanning it out unless it is transport diagnostics."""
        message = self.queue(db, sender, recipient, body, task_id, kind="system")
        if broadcast:
            self.fanout(db, message, sender, recipient, body, task_id, kind="system")
        return message

    def notice(self, sender, recipient, body):
        with self.tx() as db:
            return self.notify(db, sender, recipient, body, broadcast=False)

    def send(self, actor, recipient, body, task_id=None, message_id=None, *, knowledge_id=None):
        with self.tx() as db:
            existing = bool(message_id and db.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone())
            message = self.queue(db, actor, recipient, body, task_id, message_id, knowledge_id=knowledge_id)
            if not existing:
                self.fanout(db, message, actor, recipient, body, task_id, knowledge_id, "task" if task_id else "peer")
            return message

    def broadcast_gateway_prompt(self, body, key):
        """Queue gateway prompt text to every non-gateway member; its content never grants worker authority."""
        self.main_only(GATEWAY)
        if not isinstance(key, str) or not key:
            raise RoomError("Admin notification requires a stable prompt key")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        targets = [name for name in MEMBERS if name != GATEWAY]
        with self.tx() as db:
            inserted = False
            for target in targets:
                child = hashlib.sha256(f"{key_hash}\0{target}".encode("utf-8")).hexdigest()[:36]
                message_id = "M-" + child
                inserted = inserted or not db.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone()
                self.queue(db, GATEWAY, target, body, message_id=message_id, admin_relay=True)
            if inserted:
                self.event(db, "gateway.message.broadcast", {"key_hash": key_hash, "members": targets})
            room = self.get_room(db)
            status = room["status"]
            member_status = {name: json.loads(db.execute("SELECT data FROM members WHERE name=?", (name,)).fetchone()[0])["status"]
                             for name in targets}
            eligible = [name for name in targets if name in MODES[room["mode"]]]
        return {"members": targets, "room_status": status, "member_status": member_status,
                "eligible_members": eligible}

    def knowledge_reference(self, db, context):
        """Compare a queued lesson reference with its current revision in this read."""
        context = json.loads(context) if isinstance(context, str) else context
        reference = context.get("knowledge")
        if not reference:
            return None
        record = self.record(db, "knowledge", reference["id"])
        return {"id": record["id"], "queued_version": reference["version"], "current_version": record["version"],
                "changed": record["version"] != reference["version"], "state": record["state"],
                "basis": record["basis"], "title": bounded(record["title"], 160),
                "read_command": f"agent-room knowledge show {record['id']}",
                "history_command": f"agent-room knowledge history {record['id']}"}

    def inbox(self, actor, after=0, limit=50, *, pending=False, compact=False):
        if after < 0 or not 1 <= limit <= 200:
            raise RoomError("Use after >= 0 and limit 1..200")
        # Filter before pagination; accepted/submitted/unknown are still unprocessed.
        selection = " AND status!='processed'" if pending else ""
        with self.read() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM messages WHERE recipient=? AND seq>?{selection} ORDER BY seq LIMIT ?", (actor, after, limit+1))]
            for row in rows:
                row["context"] = json.loads(row["context"])
                row["stale"] = bool(row["task"] and self.record(db, "tasks", row["task"])["version"] != row["context"].get("task_version"))
                reference = self.knowledge_reference(db, row["context"])
                if reference:
                    row["knowledge_reference"] = reference
                if compact:
                    for field in ("body", "detail"):
                        row[field + "_preview"] = bounded(row.pop(field))
                    # A processing ACK must not make the full-message pointer skip this row.
                    row["read_command"] = f"agent-room inbox --after {row['seq'] - 1} --limit 1"
            return {"items": rows[:limit], "next_after": rows[limit-1]["seq"] if len(rows) > limit else None}

    def acknowledge(self, actor, message_id, evidence):
        if not evidence.strip():
            raise RoomError("Describe what was processed, not merely transport acceptance")
        with self.tx() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row or row["recipient"] != actor:
                raise RoomError("Only the recipient may acknowledge this message", "authority")
            db.execute("UPDATE messages SET status='processed', detail=? WHERE id=?", (evidence, message_id))
            for row in db.execute("SELECT id,data FROM attempts WHERE message=?", (message_id,)).fetchall():
                attempt = json.loads(row["data"])
                attempt.update(processed=now(), processing_evidence=evidence)
                self.save_attempt(db, attempt)

    @staticmethod
    def save_attempt(db, attempt):
        attempt["updated"] = now()
        db.execute("UPDATE attempts SET data=? WHERE id=?", (dumps(attempt), attempt["id"]))

    def begin_attempt(self, message, generation):
        with self.tx() as db:
            room = self.get_room(db)
            if room["generation"] != generation or room["status"] not in {"starting", "running"}:
                return None
            changed = db.execute("UPDATE messages SET status='dispatching' WHERE id=? AND status='queued'", (message["id"],)).rowcount
            if not changed:
                return None
            member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (message["recipient"],)).fetchone()[0])
            task = self.record(db, "tasks", message["task"]) if message["task"] else None
            attempt = {"id": uid("E-"), "message": message["id"], "task": message["task"], "member": message["recipient"],
                       "generation": generation, "native_id": member["native_id"], "turn_id": None,
                       "task_version": message.get("context_pack", {}).get("task", {}).get("version", task["version"] if task else None),
                       "context_digest": message.get("context_pack", {}).get("digest"), "state": "dispatching", "outputs": [],
                       "output_state": "not_observed", "processed": None, "created": now(), "updated": now()}
            reference = self.knowledge_reference(db, message["context"])
            if reference:
                attempt["knowledge_reference"] = reference
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?)", (attempt["id"], message["id"], message["task"], message["recipient"], generation, dumps(attempt)))
            return attempt

    def finish_dispatch(self, attempt_id, state, detail, turn_id=None):
        with self.tx() as db:
            attempt = self.entry(db, "attempts", attempt_id)
            attempt.update(state=state, detail=detail, turn_id=turn_id)
            self.save_attempt(db, attempt)
            # A model can ACK before the RPC response arrives; retain that processing receipt.
            db.execute("UPDATE messages SET status=?,detail=? WHERE id=? AND status='dispatching'", (state, detail, attempt["message"]))

    def activity_report(self):
        """Read-only activity counters per member. They are not proof that a member woke, read or processed anything.

        Enqueued messages, dispatch attempts/results, and processing ACKs are reported separately. Token use is not
        available from this ledger.
        """
        with self.read() as db:
            report = {}
            for name in MEMBERS:
                message_states = {row["status"]: row["count"] for row in db.execute(
                    "SELECT status, count(*) AS count FROM messages WHERE recipient=? GROUP BY status", (name,))}
                attempt_states = {}
                for row in db.execute("SELECT data FROM attempts WHERE member=?", (name,)):
                    state = json.loads(row[0]).get("state", "unknown")
                    attempt_states[state] = attempt_states.get(state, 0) + 1
                fanouts = sum(name in json.loads(row["data"]).get("members", []) for row in db.execute(
                    "SELECT data FROM events WHERE kind IN ('message.broadcast','gateway.message.broadcast')"))
                report[name] = {"messages_enqueued": sum(message_states.values()), "messages_by_status": message_states,
                                "dispatch_attempts": sum(attempt_states.values()), "attempts_by_result": attempt_states,
                                "processed_acks": message_states.get("processed", 0), "broadcasts_enqueued": fanouts}
            return report

    def observe_peer_prompt(self, actor, session, message_id, sender):
        """Record prompt-text evidence for a bound session; source text does not prove peer origin."""
        if actor not in MEMBERS or not session:
            return False
        if acting_member() != actor:
            return False
        with self.tx() as db:
            room = self.get_room(db)
            if actor == GATEWAY:
                owner = room.get("owner") or {}
                bound = owner.get("session") == session
            else:
                member = json.loads(db.execute("SELECT data FROM members WHERE name=?", (actor,)).fetchone()[0])
                token = os.environ.get("AGENT_ROOM_BINDING", "")
                token_matches = bool(token and member.get("token_hash") == hashlib.sha256(token.encode()).hexdigest())
                bound = (token_matches and actor in MODES[room["mode"]] and member.get("native_id") == session
                         and room["status"] in {"starting", "running", "stopping"})
            if not bound:
                return False

            message = db.execute("SELECT * FROM messages WHERE id=? AND recipient=? AND sender=?",
                                 (message_id, actor, sender)).fetchone()
            if not message:
                return False
            if message["status"] not in {"dispatching", "submitted", "accepted", "unknown", "failed", "processed"}:
                return False
            attempt_row = db.execute(
                "SELECT id,data FROM attempts WHERE message=? AND member=? ORDER BY rowid DESC LIMIT 1",
                (message_id, actor)).fetchone()
            if not attempt_row:
                return False
            attempt = json.loads(attempt_row["data"])
            if attempt.get("prompt_observed_at"):
                return True
            attempt.update(prompt_observed_at=now(), prompt_observation_basis="UserPromptSubmit.prompt_text")
            self.save_attempt(db, attempt)
            self.event(db, "native.prompt_envelope_observed", {"member": actor, "session": session,
                       "message": message_id, "sender": sender, "basis": "UserPromptSubmit.prompt_text"})
            return True

    def attempt_event(self, member, generation, method, params):
        with self.tx() as db:
            candidates = [json.loads(r[0]) for r in db.execute("SELECT data FROM attempts WHERE member=? AND generation=?", (member, generation))]
            turn_id = params.get("turnId") or params.get("turn", {}).get("id")
            if not turn_id and method == "item/completed":
                active = {a["turn_id"] for a in candidates if a["turn_id"] and a["state"] in {"accepted", "running", "output_received"}}
                if len(active) == 1:
                    turn_id = active.pop()
            matches = [a for a in candidates if turn_id and a["turn_id"] == turn_id]
            if params.get("threadId"):
                matches = [a for a in matches if a["native_id"] == params["threadId"]]
            event_id = self.event(db, "native.attempt_event", {"member": member, "generation": generation,
                                  "method": method, "params": params, "attempts": [a["id"] for a in matches]})
            for attempt in matches:
                if method == "turn/started" and attempt["state"] == "accepted":
                    attempt["state"] = "running"
                elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    text = params["item"].get("text", "")
                    nonempty = isinstance(text, str) and bool(text.strip())
                    attempt["outputs"].append({"event_seq": event_id, "item_id": params["item"].get("id"), "nonempty": nonempty})
                    if nonempty or attempt["output_state"] != "received":
                        attempt["output_state"] = "received" if nonempty else "empty"
                    if nonempty:
                        attempt["progress_at"] = now()
                    if attempt["state"] in {"accepted", "running"}:
                        attempt["state"] = "output_received"
                elif method == "turn/completed":
                    status = params["turn"].get("status")
                    attempt["state"] = status if status in {"completed", "failed", "interrupted"} else "unknown"
                    attempt["native_error"] = params["turn"].get("error")
                    attempt["ended"] = now()
                self.save_attempt(db, attempt)

    def interrupt_attempts(self, reason, member=None, generation=None):
        with self.tx() as db:
            for row in db.execute("SELECT data FROM attempts").fetchall():
                attempt = json.loads(row[0])
                if (member and attempt["member"] != member) or (generation and attempt["generation"] != generation):
                    continue
                if attempt["state"] in {"dispatching", "accepted", "running", "output_received", "submitted"}:
                    # ACK evidence survives; it does not prove the native turn completed.
                    attempt.update(state="unknown", detail=reason, ended=now())
                    self.save_attempt(db, attempt)

    def attempts(self, task_id=None, after=0, limit=50):
        if after < 0 or not 1 <= limit <= 200:
            raise RoomError("Use after >= 0 and limit 1..200")
        with self.read() as db:
            rows = db.execute("SELECT rowid,data FROM attempts WHERE rowid>? AND (? IS NULL OR task=?) ORDER BY rowid LIMIT ?",
                              (after, task_id, task_id, limit + 1)).fetchall()
            return {"items": [dict(json.loads(r["data"]), cursor=r["rowid"]) for r in rows[:limit]],
                    "next_after": rows[limit - 1]["rowid"] if len(rows) > limit else None}

    def _attention(self, db, tasks):
        """Derive advisory work from the caller's read snapshot; never enqueue or mutate."""
        view = {"advisory": True, "by_member": {member: [] for member in MEMBERS}}
        for task in tasks:
            if task["state"] in {"done", "cancelled"}:
                continue
            recipient = task["owner"]
            reason = "blocked_task" if task["state"] == "blocked" else "unfinished_task"
            episode = f"contract:{task['contract_revision']}"
            review = task["review_status"]
            commands = [f"agent-room task context {task['id']}"]
            if task["state"] == "review" and review.get("submission"):
                episode = review["submission"]
                reason = "review_" + review["state"]
                commands.append(f"agent-room submission show {review['submission']}")
                if review["state"] == "pending":
                    recipient, reason = review["reviewer"], "pending_review"
                elif review["state"] == "approved":
                    # Peer-required completion belongs to main, even for a worker-owned task.
                    recipient = GATEWAY
                if review.get("receipt"):
                    commands.append(f"agent-room review show {review['receipt']}")
            blockers = []
            if task["state"] == "blocked":
                blockers.append({"kind": "task", "id": task["id"],
                                 "reason": bounded(task.get("blocked_reason") or "Task is marked blocked; read current context.", 300)})
            for dependency in dict.fromkeys(task["dependencies"]):
                dep = self.record(db, "tasks", dependency)
                if dep["state"] != "done":
                    blockers.append({"kind": "dependency", "id": dependency, "state": dep["state"],
                                     "reason": "dependency_incomplete", "read_command": f"agent-room task show {dependency}"})
            for note_id, revision in task["decisions"].items():
                note = self.record(db, "notes", note_id)
                note_reason = None
                if note["version"] != revision:
                    note_reason = "decision_changed"
                elif note["state"] != "approved":
                    note_reason = "decision_unapproved"
                elif note.get("condition") and not note.get("condition_evidence"):
                    note_reason = "condition_pending"
                if note_reason:
                    blockers.append({"kind": "decision", "id": note_id, "state": note["state"],
                                     "reason": note_reason, "condition": bounded(note.get("condition", ""), 300),
                                     "read_command": f"agent-room note show {note_id}"})
            item = {"task": task["id"], "title": bounded(task["title"], 300), "state": task["state"],
                    "reason": reason, "episode": episode, "blockers": blockers, "read_commands": commands}
            if task["state"] == "review":
                item["review"] = review
            view["by_member"][recipient].append(item)
        return view

    def status(self, *, compact=False):
        with self.read() as db:
            room = self.get_room(db)
            tasks = [dict(json.loads(r["data"]), id=r["id"], version=r["version"])
                     for r in db.execute("SELECT id,version,data FROM tasks ORDER BY rowid")]
            # Closed records still contribute to counts. Compact reads need no review
            # hashing or attempt details for them; full historical inspection still does.
            visible = {t["id"]: t for t in tasks if not compact or t["state"] not in {"done", "cancelled"}}
            for task in visible.values():
                task["review_status"] = self.review_status(db, task)
                task["latest_attempt"] = None
                task["missing_evidence"] = not bool(task["evidence"])
                task["unprocessed_messages"] = 0
            # One scan supplies both global counts (including taskless peer work) and
            # task details, within the same read snapshot. Nothing is cached or ACKed.
            attempt_counts, message_counts = Counter(), Counter()
            pending_inboxes = {member: Counter() for member in MEMBERS}
            for row in db.execute("SELECT task,data FROM attempts ORDER BY rowid"):
                attempt = json.loads(row["data"])
                attempt_counts[attempt["state"]] += 1
                task = visible.get(row["task"])
                if task is not None:
                    task["latest_attempt"] = attempt
                    if attempt.get("progress_at"):
                        task["last_progress"] = max(task["last_progress"], attempt["progress_at"])
            for row in db.execute("SELECT task,recipient,status,count(*) AS count FROM messages GROUP BY task,recipient,status"):
                message_counts[row["status"]] += row["count"]
                task = visible.get(row["task"])
                if task is not None and row["status"] != "processed":
                    task["unprocessed_messages"] += row["count"]
                if row["status"] != "processed":
                    pending_inboxes[row["recipient"]][row["status"]] += row["count"]
            result = {"room": room,
                    "members": [json.loads(r[0]) for r in db.execute("SELECT data FROM members")],
                    "tasks": tasks,
                    "attention": self._attention(db, tasks),
                    "pending_inboxes": {
                        "advisory": True,
                        "by_member": {
                            member: {"count": sum(statuses.values()), "statuses": dict(statuses)}
                            for member, statuses in pending_inboxes.items() if statuses
                        },
                        "read_command": "agent-room inbox --pending --after 0",
                    },
                    "notes": [dict(json.loads(r["data"]), id=r["id"], version=r["version"])
                              for r in db.execute("SELECT id,version,data FROM notes ORDER BY rowid")],
                    "unaccounted_prompts": [dict(r) for r in db.execute("SELECT * FROM prompts WHERE accounted IS NULL")],
                    "claims": [dict(r) for r in db.execute("SELECT * FROM claims")],
                    "approvals": [json.loads(r[0]) for r in db.execute("SELECT data FROM approvals")],
                    "attempt_counts": dict(attempt_counts), "message_counts": dict(message_counts)}
            return self._compact_status(result) if compact else result

    @staticmethod
    def _compact_status(status):
        """One read-only projection. Full records and authority stay in the ledger."""
        tasks, notes = status["tasks"], status["notes"]
        status["task_counts"] = dict(Counter(t["state"] for t in tasks))
        status["note_counts"] = dict(Counter(n["state"] for n in notes))
        status["tasks"] = []
        for task in tasks:
            if task["state"] in {"done", "cancelled"}:
                continue
            item = {key: task[key] for key in ("id", "version", "state", "owner", "review_status",
                                              "last_progress", "missing_evidence", "unprocessed_messages")}
            item.update(title=bounded(task["title"], 160), next=bounded(task["next"], 240),
                        read_command=f"agent-room task context {task['id']}", latest_attempt=None)
            attempt = task["latest_attempt"]
            if attempt:
                item["latest_attempt"] = {key: attempt.get(key) for key in ("id", "state", "processed", "turn_id")}
                item["latest_attempt"]["read_command"] = f"agent-room attempt show {attempt['id']}"
            status["tasks"].append(item)
        status["notes"] = [Store._note_preview(note) for note in notes if note["state"] in {"open", "approved"}]
        status["detail"] = {"mode": "compact", "tasks": "All unfinished task identities; closed tasks counted above",
                            "notes": "Open questions/proposals and approved decisions; other states counted above",
                            "read_all_tasks": "agent-room task list --all", "read_all_notes": "agent-room note list",
                            "rule": "Previews are not full task or decision context. Follow read_command before acting; full status remains available."}
        # Keep unaccounted admin prompts, native approvals, claims and attention intact.
        return status

    def project_views(self):
        # Serialize capture + writes, so an older projection cannot replace a newer one.
        with file_lock(self.runtime / "projection.lock"):
            status = self.status()
            header = "<!-- agent-room generated; update through agent-room CLI -->\n"
            tasks = [header, "# Active tasks\n"]
            for task in status["tasks"]:
                if task["state"] not in {"done", "cancelled"}:
                    tasks.append(f"## {task['id']} — {task['title']}\n\nState: {task['state']}; owner: {task['owner']}; revision: {task['version']}\n\nReview: {task['review_status']['state']}; submission: {task['submission']}\n\nNext: {task['next']}\n\nCheckpoint: {task['checkpoint']}\n\nLast progress: {task['last_progress']}\n")
            tasks.append("\nUse agent-room task list --all for complete history.\n")
            notes = [header, "# Decisions and open questions\n"]
            for note in status["notes"]:
                if note["state"] not in {"superseded", "rejected"}:
                    notes.append(f"## {note['id']} — {note['state']}\n\n{note['body']}\n\nAnswer: {note.get('answer', '')}\n\nSource: {note.get('source', 'peer proposal; no admin approval')}\n\nCondition: {note.get('condition', '')}\n")
            for relative, content in (("tasks/active.md", tasks), ("state/current_decisions.md", notes)):
                path = self.space / relative
                if path.exists() and not path.read_text().startswith(header):
                    raise RoomError(f"Projection conflict; preserve and reconcile {path}", "conflict")
                atomic_write(path, "\n".join(content))
