import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_room.cli import parser, run
from agent_room.common import PLUGIN_ROOT
from agent_room.scaffold import initialize
from agent_room.store import Store


class CLITests(unittest.TestCase):
    def test_compact_inbox_pointer_reads_exact_full_message_after_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize(Path(directory))
            store = Store(directory)
            with store.tx() as db:
                room = store.get_room(db)
                room["owner"] = {"session": "fixture-main"}
                room["status"] = "running"
                store.put_room(db, room)
            store.member("CODEX_EXPERT", {"token_hash": hashlib.sha256(b"fixture-binding").hexdigest()})
            store.send("CLAUDE_01", "CODEX_EXPERT", "Other recipient before target")
            body = "Question à peer 🤝\n" * 100 + "Critical qualification at the end"
            message = store.send("CODEX_EXPERT", "CLAUDE_01", body)
            other = store.send("CLAUDE_01", "CODEX_EXPERT", "Other recipient after target")
            store.send("CODEX_EXPERT", "CLAUDE_01", "Later question")
            env = dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_01", AGENT_ROOM_SESSION_ID="fixture-main")
            command = [sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), "--project", directory, "--json"]
            before = store.path.read_bytes()
            result = subprocess.run(command + ["inbox", "--pending", "--compact", "--limit", "1"],
                                    env=env, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            page = json.loads(result.stdout)["data"]
            preview = page["items"][0]
            self.assertEqual(preview["id"], message["id"])
            self.assertEqual(page["next_after"], message["seq"])
            self.assertNotIn("body", preview)
            self.assertNotIn("Critical qualification", preview["body_preview"])
            self.assertNotIn("--pending", preview["read_command"])
            self.assertNotIn("--compact", preview["read_command"])
            self.assertEqual(before, store.path.read_bytes())
            # Simulate processing through a full native copy between preview and reread.
            store.acknowledge("CLAUDE_01", message["id"], "Processed the full content from native delivery")
            before = store.path.read_bytes()
            for binding, expected_id in ((env, message["id"]),
                                         (env | {"AGENT_ROOM_MEMBER": "CODEX_EXPERT", "AGENT_ROOM_BINDING": "fixture-binding"}, other["id"])):
                result = subprocess.run(command + preview["read_command"].split()[1:],
                                        env=binding, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                received = json.loads(result.stdout)["data"]["items"][0]
                self.assertEqual(received["id"], expected_id)
                if binding is env:
                    self.assertEqual(received["body"], body)
                    self.assertEqual(received["status"], "processed")
                    self.assertNotIn("body_preview", received)
            result = subprocess.run(command + preview["read_command"].split()[1:],
                                    env=env | {"AGENT_ROOM_SESSION_ID": "unbound"}, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "identity")
            self.assertEqual(before, store.path.read_bytes())

    def test_pending_inbox_entrypoint_preserves_identity_bounds_and_default_history(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize(Path(directory))
            store = Store(directory)
            with store.tx() as db:
                room = store.get_room(db)
                room["owner"] = {"session": "fixture-main"}
                store.put_room(db, room)
            old = store.send("CODEX_EXPERT", "CLAUDE_01", "An answered idea")
            current = store.send("CODEX_EXPERT", "CLAUDE_01", "A question still open")
            store.acknowledge("CLAUDE_01", old["id"], "Considered the idea")
            before = store.path.read_bytes()
            env = dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_01", AGENT_ROOM_SESSION_ID="fixture-main")
            command = [sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), "--project", directory, "--json", "inbox"]
            for flags, expected_ids in (([], [old["id"], current["id"]]), (["--pending"], [current["id"]])):
                result = subprocess.run(command + flags, env=env, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertEqual([m["id"] for m in json.loads(result.stdout)["data"]["items"]], expected_ids)
            for flags in (["--after", "-1"], ["--limit", "0"], ["--limit", "201"]):
                result = subprocess.run(command + ["--pending"] + flags, env=env, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(json.loads(result.stdout)["ok"])
            result = subprocess.run(command + ["--pending"], env=env | {"AGENT_ROOM_SESSION_ID": "unbound"},
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "identity")
            self.assertEqual(before, store.path.read_bytes())

    def test_status_stays_readable_when_sandbox_forbids_process_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize(Path(directory))
            with patch("agent_room.cli.process_alive", side_effect=PermissionError("ps blocked by sandbox")):
                data = run(parser().parse_args(["--project", directory, "status"]))
            self.assertEqual(data["room"]["status"], "stopped")
            self.assertIsNone(data["supervisor_alive"])
            self.assertTrue(all(member["process_alive"] is None for member in data["members"]))
            self.assertIn("unknown", data["process_inspection"])

    def test_invalid_arguments_have_json_error_and_no_effect(self):
        for args in (("stop", "--timeout", "-1"), ("init", "--mode", "invalid"), ("does-not-exist",)):
            result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), *args],
                cwd="/tmp", text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "arguments")

    def test_help_needs_no_room(self):
        result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "bin/agent-room"), "--help"],
            cwd="/tmp", text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("doctor", result.stdout)

    def test_review_smoke_preview_does_not_start_a_room(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts/native_smoke.py"), "--scenario", "review", "--project", directory],
                cwd=directory, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            scope = json.loads(result.stdout)
            self.assertFalse(scope["execute"])
            self.assertEqual(scope["proposed_scope"]["script_dispatches"], 2)
            self.assertEqual(list(Path(directory).iterdir()), [])
