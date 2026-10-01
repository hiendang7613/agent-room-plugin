"""PREREG gate O3: model-facing context per event class stays within a fixed byte budget.

Budgets are the sizes measured on 2026-09-30 (goal v14 campaign, plugin 0.3.20 plus fixes) plus 10%,
rounded up to 50 bytes, fixed before this check existed. A larger message fails here. To grow one, raise
its budget in the same change with a dated comment that says what the extra bytes buy; shrinking needs no
edit. Byte size is context cost only: it is not evidence of fewer model tokens, lower cost or latency.
"""

import json
import os
import unittest
from unittest.mock import patch

from agent_room import hooks
from agent_room.native import COLLABORATION_GUIDANCE, message_text, role_instructions
from agent_room.store import MAX_MESSAGE_ID_BYTES
from test_evidence import EvidenceFixture

BUDGETS = {
    "guidance": 1100,
    "worker_role_instructions": 1150,
    "delivery_taskless_overhead": 450,
    "delivery_taskless_recovery_overhead": 650,
    "delivery_task_overhead_excluding_pack": 550,
    "task_pack_compact": 750,
    "status_compact_one_task": 2700,
    "main_sessionstart_context": 1100,
    "admin_prompt_context": 300,
    "native_event_context": 150,
}


def size(text):
    return len(text.encode("utf-8"))


class ContextBudgetTests(EvidenceFixture, unittest.TestCase):
    def measurements(self):
        body = "What evidence changes your view about the retry behavior?"
        task = self.task(review_policy="none", reviewer=None)
        # Longest legal addressed IDs too: the delivered header repeats the ID, so the cap must hold for the cross
        # product of the longest ID and the longest digest, not only for generated IDs.
        taskless = self.store.send("CODEX_EXPERT", "CLAUDE_01", body, message_id="M-" + "d" * (MAX_MESSAGE_ID_BYTES - 2))
        tasked = self.store.send("CLAUDE_01", "CODEX_EXPERT", body, task["id"], message_id="M-" + "e" * (MAX_MESSAGE_ID_BYTES - 2))
        self.assertEqual((len(taskless["id"]), len(tasked["id"])), (MAX_MESSAGE_ID_BYTES, MAX_MESSAGE_ID_BYTES))
        pack = self.store.task_context(task["id"], compact=True)
        pack_text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
        with self.store.tx() as db:
            room = self.store.get_room(db)
            room["owner"] = {"session": "main"}
            self.store.put_room(db, room)
        found = {
            "guidance": size(COLLABORATION_GUIDANCE),
            "worker_role_instructions": size(role_instructions("CODEX_EXPERT")),
            "delivery_taskless_overhead": size(message_text(taskless)) - size(body),
            "delivery_taskless_recovery_overhead": size(message_text(taskless | {"pending_recovery": True})) - size(body),
            "delivery_task_overhead_excluding_pack": size(message_text(tasked | {"context_pack": pack})) - size(body) - size(pack_text),
            "task_pack_compact": size(pack_text),
            "status_compact_one_task": size(json.dumps(self.store.status(compact=True), ensure_ascii=False, separators=(",", ":"))),
        }
        payload = {"cwd": str(self.project), "session_id": "main"}
        with patch.object(hooks, "bind_main"), patch.object(hooks, "start_room", return_value={"reason": "x"}), \
                patch.object(hooks, "install_alias", return_value=False), \
                patch.dict(os.environ, AGENT_ROOM_MEMBER="CLAUDE_01", CLAUDE_ENV_FILE=""):
            def context(**fields):
                return hooks.handle(payload | fields)["hookSpecificOutput"]["additionalContext"]
            found["main_sessionstart_context"] = size(context(hook_event_name="SessionStart", source="startup"))
            found["admin_prompt_context"] = size(context(hook_event_name="UserPromptSubmit", prompt="Do the thing"))
            found["native_event_context"] = size(context(hook_event_name="UserPromptSubmit",
                                                         prompt="<task-notification>\n<summary>x</summary>\n</task-notification>"))
        return found

    def test_every_event_class_stays_within_its_budget(self):
        found = self.measurements()
        self.assertEqual(found.keys(), BUDGETS.keys(), "Every measured class needs a budget and the reverse")
        for name, budget in BUDGETS.items():
            with self.subTest(event=name):
                self.assertLessEqual(found[name], budget, f"{name} is {found[name]} bytes; budget {budget}. "
                                     "Shrink it, or raise the budget with a dated comment saying what the bytes buy.")


if __name__ == "__main__":
    unittest.main()
