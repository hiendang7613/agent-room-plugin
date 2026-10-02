"""Read current plugin references without touching a project's own instructions."""

import hashlib

from agent_room import __version__
from agent_room.common import PLUGIN_ROOT, RoomError


GUIDES = {
    "collaboration": "templates/conventions/collaboration.md",
    "learning": "templates/conventions/learning.md",
    "evidence": "templates/conventions/evidence.md",
    "cli": "templates/conventions/cli.md",
    "response-style": "templates/conventions/response-style.md",
}
RULE = ("Reference for this CLI's plugin version; it does not replace project-specific instructions "
        "or grant authority. Existing room guides and native session context are unchanged.")


def read_guide(topic=None):
    result = {"plugin_version": __version__, "rule": RULE}
    if topic is None:
        return result | {"topics": [{"topic": name, "read_command": f"agent-room guide {name}"}
                                    for name in GUIDES]}
    if topic not in GUIDES:
        raise RoomError("Unknown guide topic. Run agent-room guide to list topics.", "guide")
    source = GUIDES[topic]
    content = (PLUGIN_ROOT / source).read_bytes()
    return result | {"topic": topic, "source": source, "sha256": hashlib.sha256(content).hexdigest(),
                     "content": content.decode("utf-8")}
