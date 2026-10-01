import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_room.common import RoomError, dumps, process_stamp
from agent_room.schema import EXTENSIONS, migrate
from agent_room.scaffold import initialize
from agent_room.store import Store
from receipts import human_receipt


class EvidenceFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="room evidence ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        initialize(self.project)
        self.store = Store(self.project)
        self.source = self.project / "work.py"
        self.source.write_text("value = 1\n")
        self.prompt = human_receipt(self.store, "Implement work.py and have the expert review it")

    def task(self, **extra):
        data = dict(title="Change work", request="Implement assigned work", acceptance="Value is 1",
                    next="Read source", owner="CLAUDE_01", source=self.prompt, authority="implementation",
                    scope=["work.py"], review_policy="peer_required", reviewer="CODEX_EXPERT")
        return self.store.create_task("CLAUDE_01", data | extra)

    def current(self, task):
        with self.store.read() as db:
            return self.store.record(db, "tasks", task["id"])

    def submit(self, task):
        task = self.current(task)
        return self.store.submit_task(task["owner"], task["id"], task["version"],
                                     {"paths": ["work.py"], "evidence": ["python check verified value=1"], "summary": "Implementation ready"})["submission"]

    def review(self, submission, **extra):
        data = {"source_digest": submission["digest"], "verdict": "approve", "findings": [],
                "summary": "Inspected source and acceptance", "evidence": ["Read work.py; value equals 1"]}
        return self.store.record_review("CODEX_EXPERT", submission["id"], data | extra)

    def finish(self, task):
        task = self.current(task)
        return self.store.update_task("CLAUDE_01", task["id"], task["version"], {"state": "done"})


class EvidenceTests(EvidenceFixture, unittest.TestCase):

    def test_required_review_and_allowed_completion_survive_reload(self):
        task = self.task()
        with self.assertRaises(RoomError):
            self.store.update_task("CLAUDE_01", task["id"], 1, {"state": "done", "evidence": ["I say it passed"]})
        submission = self.submit(task)
        with self.assertRaises(RoomError):
            self.finish(task)
        receipt = self.review(submission)
        self.store = Store(self.project)
        status = self.store.status()["tasks"][0]
        self.assertEqual(status["review_status"]["receipt"], receipt["id"])
        self.assertEqual(self.finish(task)["state"], "done")

    def test_self_review_wrong_reviewer_and_digest_are_rejected(self):
        task = self.task()
        submission = self.submit(task)
        data = {"source_digest": submission["digest"], "verdict": "approve", "findings": [],
                "summary": "Pass", "evidence": ["Source checked"]}
        for actor in ("CLAUDE_01", "CODEX_01"):
            with self.assertRaises(RoomError):
                self.store.record_review(actor, submission["id"], data)
        with self.assertRaises(RoomError):
            self.review(submission, source_digest="an old digest")
        self.assertEqual(self.store.status()["tasks"][0]["review_status"]["state"], "pending")

    def test_source_drift_after_approval_blocks_done_and_new_submission_recovers(self):
        task = self.task()
        first = self.submit(task)
        self.review(first)
        self.source.write_text("value = 2\n")
        self.assertEqual(self.store.status()["tasks"][0]["review_status"]["state"], "stale")
        with self.assertRaises(RoomError):
            self.finish(task)
        second = self.submit(task)
        self.assertNotEqual(second["digest"], first["digest"])
        with self.assertRaises(RoomError):
            self.review(first)
        self.review(second, evidence=["Reviewed updated source and new evidence"])
        self.assertEqual(self.finish(task)["state"], "done")

    def test_changes_requested_cannot_be_rewritten_as_approval(self):
        task = self.task()
        submission = self.submit(task)
        receipt = self.review(submission, verdict="changes_requested", findings=[
            {"summary": "Acceptance needs another check", "severity": "medium", "path": "work.py", "line": 1}])
        with self.assertRaises(RoomError):
            self.review(submission)
        with self.assertRaises(RoomError):
            self.finish(task)
        with self.store.read() as db:
            self.assertEqual(self.store.entry(db, "reviews", receipt["id"])["verdict"], "changes_requested")

    def test_missing_artifact_decision_change_and_permission_change_invalidate_review(self):
        task = self.task()
        first = self.submit(task)
        self.source.unlink()
        with self.assertRaises(RoomError):
            self.review(first)
        self.source.write_text("value = 1\n")
        self.review(first)
        note = self.store.add_note("CLAUDE_01", {"kind": "decision", "body": "New acceptance constraint", "tasks": [task["id"]], "source": self.prompt})
        self.assertIn(note["id"], self.current(task)["decisions"])
        with self.assertRaises(RoomError):
            self.finish(task)
        second = self.submit(task)
        self.review(second)
        current = self.current(task)
        self.store.update_task("CLAUDE_01", task["id"], current["version"], {"acceptance": "Changed acceptance", "source": self.prompt})
        with self.assertRaises(RoomError):
            self.finish(task)

    def test_submission_cannot_supply_hashes_or_invent_an_all_missing_artifact(self):
        task = self.task()
        base = {"paths": ["not-there.py"], "evidence": ["Test passed"], "summary": "Proposed result"}
        for data in (base, base | {"snapshot": {"work.py": "forged"}}, base | {"paths": []}):
            with self.assertRaises(RoomError):
                self.store.submit_task("CLAUDE_01", task["id"], task["version"], data)
        link = self.project / "alias.py"
        link.symlink_to(self.source)
        with self.assertRaises(RoomError):
            self.store.submit_task("CLAUDE_01", task["id"], 1, base | {"paths": ["alias.py"]})

    def test_submit_preserves_dependency_and_conditional_approval_gates(self):
        dependency = self.task(review_policy="none", reviewer=None)
        task = self.task(dependencies=[dependency["id"]])
        with self.assertRaises(RoomError):
            self.submit(task)
        self.store.update_task("CLAUDE_01", dependency["id"], 1, {"state": "done", "evidence": ["Checked prerequisite"]})
        self.store.add_note("CLAUDE_01", {"kind": "decision", "body": "Proceed after a condition", "condition": "Unmet condition",
            "source": self.prompt, "tasks": [task["id"]]})
        with self.assertRaises(RoomError):
            self.submit(task)
        self.assertIsNone(self.current(task)["submission"])

    def test_changed_evidence_in_same_done_update_cannot_reuse_review(self):
        task = self.task()
        self.review(self.submit(task))
        task = self.current(task)
        with self.assertRaises(RoomError):
            self.store.update_task("CLAUDE_01", task["id"], task["version"], {"state": "done", "evidence": ["Different unreviewed evidence"]})
        self.assertEqual(self.finish(task)["state"], "done")

    def test_advisory_proposal_keeps_review_valid_and_does_not_block_work(self):
        task = self.task()
        self.review(self.submit(task))
        before = self.current(task)
        proposal = self.store.add_note("CODEX_EXPERT", {
            "kind": "proposal", "body": "Could we simplify the next iteration?",
            "tasks": [task["id"]], "condition": "Explore if useful"})
        self.assertEqual(self.current(task), before)
        self.assertEqual(self.store.status()["tasks"][0]["review_status"]["state"], "approved")
        self.store.resolve_note("CLAUDE_01", proposal["id"], 1, {
            "state": "rejected", "answer": "Keep the accepted implementation", "source": self.prompt})
        self.assertEqual(self.current(task), before)
        self.assertEqual(self.finish(task)["state"], "done")

        unfinished = self.task(review_policy="none", reviewer=None)
        self.store.add_note("CODEX_EXPERT", {"kind": "proposal", "body": "Another possible approach",
                                               "tasks": [unfinished["id"]]})
        self.assertEqual(self.store.claim("CLAUDE_01", unfinished["id"], unfinished["version"])["owner"], "CLAUDE_01")

    def test_approved_proposal_binds_condition_and_invalidates_earlier_review(self):
        task = self.task()
        self.review(self.submit(task))
        proposal = self.store.add_note("CODEX_EXPERT", {
            "kind": "proposal", "body": "Require the additional fixture", "tasks": [task["id"]],
            "condition": "Additional fixture passes"})
        approved = self.store.resolve_note("CLAUDE_01", proposal["id"], 1, {
            "state": "approved", "answer": "Require it for this task", "source": self.prompt})
        self.assertEqual(self.current(task)["decisions"], {proposal["id"]: approved["version"]})
        self.assertEqual(self.store.status()["tasks"][0]["review_status"]["state"], "stale")
        with self.assertRaises(RoomError):
            self.submit(task)
        self.store.resolve_note("CLAUDE_01", proposal["id"], approved["version"], {
            "answer": "Fixture verified", "condition_evidence": "Fixture passed", "source": self.prompt})
        self.review(self.submit(task))
        self.assertEqual(self.finish(task)["state"], "done")

    def test_legacy_bound_proposal_requires_explicit_resolution_after_reload(self):
        task = self.task(review_policy="none", reviewer=None)
        proposal = self.store.add_note("CODEX_EXPERT", {"kind": "proposal", "body": "Legacy pending constraint",
                                               "tasks": [task["id"]]})
        # Persist the binding written by <=0.2.1; upgrading must not silently release it.
        with self.store.tx() as db:
            current = self.store.record(db, "tasks", task["id"])
            current["decisions"] = {proposal["id"]: 1}
            current["contract_revision"] += 1
            self.store.save(db, "tasks", current, current["version"])
        self.store = Store(self.project)
        with self.assertRaises(RoomError):
            self.store.claim("CLAUDE_01", task["id"], self.current(task)["version"])
        self.store.resolve_note("CLAUDE_01", proposal["id"], 1, {
            "state": "superseded", "answer": "Retire the old gate; keep this as historical discussion", "source": self.prompt})
        current = self.current(task)
        self.assertEqual(current["decisions"], {})
        self.assertEqual(self.store.claim("CLAUDE_01", task["id"], current["version"])["owner"], "CLAUDE_01")

    def test_concurrent_submissions_have_one_winner_and_only_owner_releases_claim(self):
        task = self.task()
        claim = self.store.claim("CLAUDE_01", task["id"], 1)
        def submit():
            try:
                return self.store.submit_task("CLAUDE_01", task["id"], claim["version"],
                    {"paths": ["work.py"], "evidence": ["Verified"], "summary": "Ready"})
            except RoomError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("conflict", results)
        self.assertEqual(self.store.status()["claims"], [])

    def test_checkpoint_sequences_reconcile_generation_source_and_owner(self):
        task = self.task()
        data = {"summary": "Inspected file", "last_safe_action": "Read source", "next": "Reconcile operation",
                "paths": ["work.py"], "unknown_effects": ["External operation E1 may have completed"]}
        checkpoint = self.store.checkpoint("CLAUDE_01", task["id"], 1, data)
        self.assertEqual(checkpoint["sequence"], 1)
        with self.assertRaises(RoomError):
            self.store.checkpoint("CLAUDE_01", task["id"], 1, data)
        self.store = Store(self.project)
        self.assertEqual(self.store.task_context(task["id"])["checkpoint"]["unknown_effects"], data["unknown_effects"])
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["generation"] = "next-native-launch"
            self.store.put_room(db, room)
        self.source.write_text("value = 3\n")
        pack = self.store.task_context(task["id"])
        self.assertEqual(len(pack["checkpoint_reconcile"]), 2)
        second = self.store.checkpoint("CLAUDE_01", task["id"], self.current(task)["version"], data)
        self.assertEqual(second["sequence"], 2)
        task = self.current(task)
        self.store.update_task("CLAUDE_01", task["id"], task["version"], {"owner": "CODEX_EXPERT", "reviewer": "CLAUDE_01", "source": self.prompt})
        self.assertIn("owner changed; handoff required", self.store.task_context(task["id"])["checkpoint_reconcile"])

    def test_context_pack_has_an_explicit_bound_and_read_pointers(self):
        task = self.task(request="very large input " * 5000)
        pack = self.store.task_context(task["id"])
        self.assertLess(len(dumps(pack)), 12200)
        self.assertIn("truncated", dumps(pack))
        self.assertIn("agent-room task show " + task["id"], pack["full_record_commands"])

    def test_legacy_checkpoint_update_preserves_unknown_effects_and_is_visible(self):
        task = self.task()
        self.store.checkpoint("CLAUDE_01", task["id"], 1, {"summary": "Before interruption", "last_safe_action": "Read file",
            "next": "Check effect", "paths": ["work.py"], "unknown_effects": ["Operation may have committed"]})
        self.store.update_task("CLAUDE_01", task["id"], 2, {"checkpoint": "Legacy client wrote a new summary"})
        context = self.store.task_context(task["id"])
        self.assertEqual(context["legacy_checkpoint"], "Legacy client wrote a new summary")
        self.assertEqual(context["checkpoint"]["unknown_effects"], ["Operation may have committed"])
        self.assertIn("legacy checkpoint text changed; reconcile with structured checkpoint", context["checkpoint_reconcile"])

    def test_processed_receipt_and_empty_native_completion_do_not_finish_task(self):
        task = self.task()
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room.update(status="running", generation="g1")
            self.store.put_room(db, room)
        message = self.store.send("CLAUDE_01", "CODEX_EXPERT", "Review the file", task["id"])
        attempt = self.store.begin_attempt(message, "g1")
        self.store.acknowledge("CODEX_EXPERT", message["id"], "Read and investigated")
        self.store.finish_dispatch(attempt["id"], "accepted", "RPC accepted", "turn-1")
        self.store.attempt_event("CODEX_EXPERT", "g1", "item/completed", {"turnId": "turn-1", "item": {"id": "empty", "type": "agentMessage", "text": " "}})
        self.store.attempt_event("CODEX_EXPERT", "g1", "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}})
        result = Store(self.project).attempts(task["id"])["items"][0]
        self.assertEqual((result["state"], result["output_state"]), ("completed", "empty"))
        self.assertTrue(result["processed"])
        self.assertEqual(self.current(task)["state"], "ready")
        self.assertEqual(self.store.status()["message_counts"]["processed"], 1)
        self.assertTrue(result["outputs"][0]["event_seq"])


class MigrationTests(EvidenceFixture, unittest.TestCase):
    # A legacy fixture uses the actual schema-1 tables and omits all schema-2 fields.
    def legacy(self):
        task = self.task(review_policy="none", reviewer=None)
        db = self.store.connect()
        try:
            for table in ("reviews", "submissions", "checkpoints", "attempts", "knowledge"):
                db.execute(f"DROP TABLE {table}")
            room = json.loads(db.execute("SELECT value FROM meta WHERE key='room'").fetchone()[0])
            room["schema"] = 1
            self.store.put_room(db, room)
            for key in ("review_policy", "reviewer", "submission", "checkpoint_id", "contract_revision", "last_progress"):
                task.pop(key)
            db.execute("UPDATE tasks SET data=? WHERE id=?", (dumps(task), task["id"]))
        finally:
            db.close()
        return task

    def test_legacy_backup_and_idempotent_upgrade(self):
        before = self.legacy()
        with self.assertRaises(RoomError):
            self.store.status()
        result = migrate(self.store)
        backup = sqlite3.connect(result["backup"])
        try:
            self.assertEqual(json.loads(backup.execute("SELECT value FROM meta WHERE key='room'").fetchone()[0])["schema"], 1)
            self.assertEqual(json.loads(backup.execute("SELECT data FROM tasks").fetchone()[0]), before)
        finally:
            backup.close()
        after = self.current(before)
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(after["review_policy"], "none")
        self.assertFalse(migrate(self.store)["migrated"])
        self.assertEqual(len(list((self.store.runtime / "backups").glob("*.sqlite3"))), 1)

    def test_upgrade_failure_rolls_back_and_retains_backup(self):
        self.legacy()
        with patch("agent_room.schema.EXTENSIONS", EXTENSIONS + "INVALID SQL;"):
            with self.assertRaises(sqlite3.Error):
                migrate(self.store)
        db = self.store.connect()
        try:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='room'").fetchone()[0])["schema"], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='submissions'").fetchone())
        finally:
            db.close()
        self.assertEqual(len(list((self.store.runtime / "backups").glob("*.sqlite3"))), 1)
        self.assertTrue(migrate(self.store)["migrated"])

    def test_upgrade_rejects_stale_room_with_live_worker(self):
        self.legacy()
        db = self.store.connect()
        try:
            member = json.loads(db.execute("SELECT data FROM members WHERE name='CODEX_EXPERT'").fetchone()[0])
            member.update(pid=os.getpid(), stamp=process_stamp(os.getpid()))
            db.execute("UPDATE members SET data=? WHERE name='CODEX_EXPERT'", (dumps(member),))
        finally:
            db.close()
        with self.assertRaises(RoomError):
            migrate(self.store)
        self.assertFalse((self.store.runtime / "backups").exists())


if __name__ == "__main__":
    unittest.main()
