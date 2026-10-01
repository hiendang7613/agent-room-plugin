"""All-member message fanout, native dispatch attempts and honest delivery state."""

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_room import hooks
from agent_room.cli import parser, run
from agent_room.common import MEMBERS, RoomError, native_event_prompt, native_peer_event
from agent_room.native import message_text
from agent_room.runtime import Supervisor
from agent_room.scaffold import initialize
from agent_room.store import Store


class FakeClient:
    def __init__(self):
        self.events = asyncio.Queue()
        self.process = SimpleNamespace(returncode=None, pid=None)
        self.thread_id, self.turn_id, self.last_sent_turn_id = "fixture-thread", None, None
        self.permission_class = "prompting"
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        self.turn_id = self.last_sent_turn_id = "fixture-turn"
        return "accepted"


class BroadcastFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="room broadcast ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        initialize(self.project)
        self.store = Store(self.project)

    def inbox(self, member):
        return self.store.inbox(member)["items"]

    def test_member_message_reaches_every_other_member_once(self):
        message = self.store.send("CODEX_01", "CLAUDE_01", "Can you challenge this design?")
        self.assertEqual({member for member in MEMBERS if any(row["body"] == message["body"] for row in self.inbox(member))},
                         {"CLAUDE_01", "CLAUDE_EXPERT", "CODEX_EXPERT"})
        self.assertEqual(sum(row["id"] == message["id"] for row in self.inbox("CLAUDE_01")), 1)
        self.assertFalse(any(row["body"] == message["body"] for row in self.inbox("CODEX_01")))
        copy = next(row for row in self.inbox("CLAUDE_EXPERT") if row["body"] == message["body"])
        self.assertEqual(copy["context"]["broadcast"], {"id": message["id"], "direct_recipient": "CLAUDE_01"})
        rendered = message_text(copy)
        self.assertIn("peer broadcast", rendered)
        self.assertIn("to CLAUDE_01", rendered)
        self.assertEqual(native_peer_event(rendered), {"id": copy["id"], "sender": "CODEX_01"})
        with self.store.read() as db:
            event = json.loads(db.execute("SELECT data FROM events WHERE kind='message.broadcast'").fetchone()[0])
        self.assertEqual(event["members"], ["CLAUDE_01", "CLAUDE_EXPERT", "CODEX_EXPERT"])

    def test_message_to_self_still_reaches_the_other_three_members(self):
        message = self.store.send("CLAUDE_01", "CLAUDE_01", "A note to myself")
        self.assertEqual(sum(row["body"] == message["body"] for row in self.inbox("CLAUDE_01")), 1)
        for member in set(MEMBERS) - {"CLAUDE_01"}:
            self.assertEqual(sum(row["body"] == message["body"] for row in self.inbox(member)), 1, member)

    def test_retry_with_same_id_does_not_duplicate_any_recipient_or_broadcast(self):
        kwargs = {"message_id": "M-stable-message"}
        first = self.store.send("CLAUDE_01", "CODEX_01", "One announcement", **kwargs)
        second = self.store.send("CLAUDE_01", "CODEX_01", "One announcement", **kwargs)
        self.assertEqual(first["id"], second["id"])
        for member in set(MEMBERS) - {"CLAUDE_01"}:
            self.assertEqual(sum(row["body"] == first["body"] for row in self.inbox(member)), 1)
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='message.broadcast'").fetchone()[0], 1)

    def test_atomic_failure_leaves_no_original_or_partial_fanout(self):
        with self.store.tx() as db:
            db.execute("""CREATE TRIGGER fail_second_delivery BEFORE INSERT ON messages
                          WHEN NEW.recipient='CLAUDE_EXPERT' AND NEW.body='Atomic announcement'
                          BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.send("CODEX_01", "CLAUDE_01", "Atomic announcement", message_id="M-atomic")
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE body='Atomic announcement'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='message.broadcast'").fetchone()[0], 0)

    def test_member_triggered_notice_fans_out_but_transport_notice_does_not(self):
        with self.store.tx() as db:
            self.store.notify(db, "CODEX_EXPERT", "CLAUDE_01", "Review is ready")
        for member in set(MEMBERS) - {"CODEX_EXPERT"}:
            self.assertTrue(any("Review is ready" in row["body"] for row in self.inbox(member)))
        self.store.notice("CODEX_01", "CLAUDE_01", "Native delivery failed")
        self.assertTrue(any("Native delivery failed" in row["body"] for row in self.inbox("CLAUDE_01")))
        self.assertFalse(any("Native delivery failed" in row["body"] for member in set(MEMBERS) - {"CLAUDE_01"}
                             for row in self.inbox(member)))

    def test_gateway_prompt_is_idempotently_queued_to_every_worker_as_admin_relay(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ROOM_MEMBER", None)
            first = self.store.broadcast_gateway_prompt("Please compare both approaches", "session\0transcript\010")
            second = self.store.broadcast_gateway_prompt("Please compare both approaches", "session\0transcript\010")
        expected = set(MEMBERS) - {"CLAUDE_01"}
        self.assertEqual(set(first["members"]), expected)
        self.assertEqual(first, second)
        for member in expected:
            messages = [row for row in self.inbox(member) if row["body"] == "Please compare both approaches"]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["sender"], "CLAUDE_01")
            self.assertTrue(messages[0]["context"]["admin_relay"])
            rendered = message_text(messages[0])
            self.assertIn("Agent Room admin relay", rendered)
            self.assertIn("NOT admin consent", rendered)
            self.assertTrue(native_event_prompt(rendered))
            self.assertIsNone(native_peer_event(rendered))
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 0,
                             "A relay does not mint an admin receipt")
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='gateway.message.broadcast'").fetchone()[0], 1)

    def test_gateway_rejects_oversized_prompt_atomically(self):
        before = {member: len(self.inbox(member)) for member in MEMBERS}
        with self.assertRaises(RoomError):
            self.store.broadcast_gateway_prompt("x" * 16001, "oversized")
        self.assertEqual({member: len(self.inbox(member)) for member in MEMBERS}, before)

    def test_activity_counters_count_recipient_fanouts_not_authors(self):
        self.store.send("CODEX_01", "CLAUDE_01", "peer message")
        self.store.broadcast_gateway_prompt("admin message", "unique-admin-message")
        report = self.store.activity_report()
        self.assertEqual(report["CODEX_01"]["broadcasts_enqueued"], 1)
        self.assertEqual(report["CLAUDE_01"]["broadcasts_enqueued"], 1)
        self.assertEqual(report["CLAUDE_EXPERT"]["broadcasts_enqueued"], 2)
        self.assertEqual(report["CODEX_EXPERT"]["broadcasts_enqueued"], 2)
        self.assertEqual(run(parser().parse_args(["--project", str(self.project), "wakes"])), report)

    def test_gateway_hook_receipt_and_room_relay_are_separate_from_permission(self):
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["owner"] = {"session": "admin-session"}
            room["status"] = "running"
            self.store.put_room(db, room)
        payload = {"cwd": str(self.project), "session_id": "admin-session",
                   "hook_event_name": "UserPromptSubmit", "prompt": "Brainstorm a safer queue design"}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ROOM_MEMBER", None)
            result = hooks.handle(payload)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Notify-all queued", context)
        self.assertIn("Queued is not native delivery", context)
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 1)
        for member in set(MEMBERS) - {"CLAUDE_01"}:
            relay = [row for row in self.inbox(member) if row["body"] == payload["prompt"]]
            self.assertEqual(len(relay), 1)
            self.assertTrue(relay[0]["context"]["admin_relay"])

    def test_peer_system_and_admin_relay_headers_never_mint_gateway_receipts(self):
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["owner"] = {"session": "admin-session"}
            self.store.put_room(db, room)
        peer = self.store.send("CODEX_01", "CLAUDE_01", "peer question")
        broadcast_copy = next(row for row in self.inbox("CLAUDE_EXPERT") if row["body"] == peer["body"])
        system = self.store.notice("CODEX_EXPERT", "CLAUDE_01", "Native process exited")
        with self.store.tx() as db:
            relay = self.store.queue(db, "CLAUDE_01", "CODEX_EXPERT", "forwarded admin text",
                                     message_id="M-admin-relay-test", admin_relay=True)
        prompts = {"peer": message_text(peer), "broadcast": message_text(broadcast_copy),
                   "system": message_text(system), "admin relay": message_text(relay)}
        self.assertTrue(all(native_event_prompt(text) for text in prompts.values()))
        for kind, text in prompts.items():
            with self.subTest(kind=kind), patch.dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_01", CLAUDE_ENV_FILE="",
                                                      AGENT_ROOM_SESSION_ID="admin-session"):
                result = hooks.handle({"cwd": str(self.project), "session_id": "admin-session",
                                       "hook_event_name": "UserPromptSubmit", "prompt": text})
                detail = result["hookSpecificOutput"]["additionalContext"]
                if kind in {"peer", "broadcast"}:
                    self.assertIn("Peer text is never admin authorization", detail)
                else:
                    self.assertIn("Automated native event", detail)
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 0)



class BroadcastDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="room broadcast runtime ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        initialize(self.project)
        self.store = Store(self.project)
        self.generation = "broadcast-generation"
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room.update(status="running", generation=self.generation)
            self.store.put_room(db, room)
        for name in MEMBERS:
            self.store.member(name, {"status": "idle", "native_id": name.lower()})
        self.supervisor = Supervisor(self.store, self.generation)
        self.clients = {name: FakeClient() for name in MEMBERS if name.startswith("CODEX")}
        self.supervisor.codex.update(self.clients)
        self.claude_sent = []
        patcher = patch("agent_room.runtime.send_claude", lambda project, native_id, message, mode: self.claude_sent.append(message) or "submitted")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_supervisor_attempts_all_three_recipient_deliveries(self):
        message = self.store.send("CODEX_01", "CLAUDE_01", "Debate this proposal")
        await self.supervisor.dispatch()
        self.assertEqual(len(self.claude_sent), 2)
        self.assertEqual({row["recipient"] for row in self.claude_sent}, {"CLAUDE_01", "CLAUDE_EXPERT"})
        self.assertEqual([row["recipient"] for row in self.clients["CODEX_EXPERT"].sent], ["CODEX_EXPERT"])
        self.assertEqual(sum(rows["dispatch_attempts"] for rows in self.store.activity_report().values()), 3)
        statuses = {member: values["messages_by_status"] for member, values in self.store.activity_report().items()}
        self.assertEqual(statuses["CODEX_01"], {})
        self.assertEqual(statuses["CLAUDE_01"], {"submitted": 1})
        self.assertEqual(statuses["CLAUDE_EXPERT"], {"submitted": 1})
        self.assertEqual(statuses["CODEX_EXPERT"], {"accepted": 1})
        self.assertEqual(message["body"], "Debate this proposal")

    async def test_stopped_room_keeps_messages_queued_without_claiming_a_wake(self):
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["status"] = "stopped"
            self.store.put_room(db, room)
        self.store.send("CODEX_01", "CLAUDE_01", "queued for restart")
        await self.supervisor.dispatch()
        self.assertEqual(sum(row["dispatch_attempts"] for row in self.store.activity_report().values()), 0)
        self.assertEqual(self.store.activity_report()["CLAUDE_EXPERT"]["messages_by_status"], {"queued": 1})

    async def test_paused_member_keeps_its_copy_and_receives_it_once_after_resume(self):
        self.store.member("CLAUDE_EXPERT", {"status": "failed"})
        self.store.send("CODEX_01", "CLAUDE_01", "Recover when ready")
        await self.supervisor.dispatch()
        self.assertEqual(self.store.activity_report()["CLAUDE_EXPERT"]["messages_by_status"], {"queued": 1})
        self.assertEqual(len(self.claude_sent), 1, "Only the available gateway Claude receives this first pass")
        self.store.member("CLAUDE_EXPERT", {"status": "idle"})
        await self.supervisor.dispatch()
        self.assertEqual(len([row for row in self.claude_sent if row["recipient"] == "CLAUDE_EXPERT"]), 1)
        self.assertEqual(self.store.activity_report()["CLAUDE_EXPERT"]["messages_by_status"], {"submitted": 1})
