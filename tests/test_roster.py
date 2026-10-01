"""R1: the room's members and the admin gateway are data (agent_room/roster.py), and behavior is unchanged.

The admin's names CLAUDE_WORKER and CODEX_WORKER work as aliases of the stable ids; the intended model and effort
(all xhigh) are recorded but not applied at launch; exactly one member is the gateway; and no module other than the
roster spells a member id as a code literal, so the hardcoding cannot grow back unnoticed.
"""

import inspect
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent_room import native
from agent_room.cli import parser, run
from agent_room.common import GATEWAY, MEMBERS, MODES, RoomError, acting_member, canonical_member
from agent_room.roster import ALIASES, DEFAULT_MEMBERS, LAUNCHED_CLAUDE, ROSTER
from test_evidence import EvidenceFixture


class RosterTests(EvidenceFixture, unittest.TestCase):
    def test_modes_and_members_are_what_they_were(self):
        self.assertEqual(MODES["default"], ("CLAUDE_01", "CODEX_EXPERT"))
        self.assertEqual(MODES["full"], ("CLAUDE_01", "CODEX_01", "CLAUDE_EXPERT", "CODEX_EXPERT"))
        self.assertEqual((MEMBERS, DEFAULT_MEMBERS, GATEWAY, LAUNCHED_CLAUDE), (MODES["full"], MODES["default"], "CLAUDE_01", "CLAUDE_EXPERT"))

    def test_the_roster_is_consistent_data(self):
        names = [member["name"] for member in ROSTER]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(sum(member["gateway"] for member in ROSTER), 1)
        self.assertEqual({member["host"] for member in ROSTER}, {"claude", "codex"})
        self.assertEqual(sorted(member["role"] for member in ROSTER), ["expert", "expert", "worker", "worker"])
        self.assertEqual(ALIASES, {"CLAUDE_WORKER": "CLAUDE_01", "CODEX_WORKER": "CODEX_01"})
        self.assertFalse(set(ALIASES) & set(names))  # An alias never shadows an id.

    def test_intended_defaults_are_recorded_but_not_applied_at_launch(self):
        self.assertEqual({member["name"]: member["intended"] for member in ROSTER},
                         {"CLAUDE_01": {"model": "Sonnet 5.5", "effort": "xhigh"}, "CODEX_01": {"model": "Luna 6", "effort": "xhigh"},
                          "CLAUDE_EXPERT": {"model": "Opus 5.5", "effort": "xhigh"}, "CODEX_EXPERT": {"model": "Sol 6.1", "effort": "xhigh"}})
        self.assertTrue(all(member["model"] is None and member["effort"] is None for member in ROSTER))  # Inherit until a launch step applies them.
        self.assertNotIn("--model", inspect.getsource(native.start_claude))
        self.assertNotIn("--effort", inspect.getsource(native.start_claude))

    def test_aliases_work_at_the_cli_and_in_the_environment(self):
        args = parser().parse_args(["--project", str(self.project), "send", "--to", "CODEX_WORKER", "--body", "hello"])
        self.assertEqual(args.to, "CODEX_01")
        args = parser().parse_args(["--project", str(self.project), "task", "list", "--owner", "CLAUDE_WORKER"])
        self.assertEqual(args.owner, "CLAUDE_01")
        self.assertEqual(canonical_member("CLAUDE_01"), "CLAUDE_01")
        self.assertEqual(canonical_member("nobody"), "nobody")
        for given, expected in (("CLAUDE_WORKER", "CLAUDE_01"), ("CODEX_WORKER", "CODEX_01"), ("CODEX_EXPERT", "CODEX_EXPERT")):
            with self.subTest(env=given), patch.dict(os.environ, AGENT_ROOM_MEMBER=given):
                self.assertEqual(acting_member(), expected)  # A non-gateway member cannot be mistaken for the default.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ROOM_MEMBER", None)
            self.assertEqual(acting_member(), GATEWAY)

    def test_an_alias_acts_as_the_gateway_and_unknown_names_are_still_refused(self):
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["owner"] = {"session": "main"}
            self.store.put_room(db, room)
        with patch.dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_WORKER", AGENT_ROOM_SESSION_ID="main"):
            self.assertEqual(self.store.actor(), "CLAUDE_01")
            self.assertEqual(run(parser().parse_args(["--project", str(self.project), "task", "list"])), [])
        with patch.dict(os.environ, AGENT_ROOM_MEMBER="NOBODY", AGENT_ROOM_SESSION_ID="main"):
            with self.assertRaises(RoomError) as caught:
                self.store.actor()
            self.assertEqual(caught.exception.code, "identity")
        with self.assertRaises(SystemExit):
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                parser().parse_args(["--project", str(self.project), "send", "--to", "NOBODY", "--body", "x"])

    def test_only_the_gateway_records_admin_intent(self):
        for member in MEMBERS:
            with self.subTest(member=member):
                if member == GATEWAY:
                    self.store.main_only(member)
                else:
                    with self.assertRaises(RoomError) as caught:
                        self.store.main_only(member)
                    self.assertEqual(caught.exception.code, "authority")
                    self.assertIn(GATEWAY, str(caught.exception))

    def test_no_module_but_the_roster_spells_a_member_id_as_a_literal(self):
        """Ratchet against hardcoding: code uses GATEWAY, MEMBERS and the roster, never a quoted member id."""
        pattern = re.compile(r"""["'](CLAUDE_01|CODEX_01|CLAUDE_EXPERT|CODEX_EXPERT)["']""")
        offenders = []
        for path in sorted((Path(__file__).resolve().parents[1] / "agent_room").glob("*.py")):
            if path.name == "roster.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()[:90]}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
