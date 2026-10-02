"""Roster identity and requested native model settings live in one data table.

The gateway is the existing host session, so its model/effort are observed from the host when available, not changed by
the room. Spawned members receive their configured model/effort at the native boundary. Stable ledger ids do not
change; aliases are another name for the same member. A Codex-hosted gateway still needs its own provenance contract.
"""

ROSTER = (
    {"name": "CLAUDE_01", "alias": "CLAUDE_WORKER", "host": "claude", "role": "worker", "gateway": True, "default_mode": True,
     "model": "sonnet", "effort": "xhigh", "label": "Sonnet 5.5", "control": "host"},
    {"name": "CODEX_01", "alias": "CODEX_WORKER", "host": "codex", "role": "worker", "gateway": False, "default_mode": True,
     "model": "gpt-6-luna", "effort": "xhigh", "label": "Luna 6", "control": "room"},
    {"name": "CLAUDE_EXPERT", "alias": None, "host": "claude", "role": "expert", "gateway": False, "default_mode": True,
     "model": "opus", "effort": "xhigh", "label": "Opus 5.5", "control": "room"},
    {"name": "CODEX_EXPERT", "alias": None, "host": "codex", "role": "expert", "gateway": False, "default_mode": True,
     "model": "gpt-6.1-sol", "effort": "xhigh", "label": "Sol 6.1", "control": "room"},
)

MEMBERS = tuple(member["name"] for member in ROSTER)
ROSTER_BY_NAME = {member["name"]: member for member in ROSTER}
DEFAULT_MEMBERS = tuple(member["name"] for member in ROSTER if member["default_mode"])
GATEWAY = next(member["name"] for member in ROSTER if member["gateway"])
ALIASES = {member["alias"]: member["name"] for member in ROSTER if member["alias"]}
LAUNCHED_CLAUDE = next(member["name"] for member in ROSTER if member["host"] == "claude" and not member["gateway"])


def canonical_member(name):
    """The stable id for a member id or alias; other text (including an empty name) is returned unchanged."""
    return ALIASES.get(name, name)


def launch_config(name):
    """Native model settings for spawned members; the existing gateway remains host-managed."""
    member = ROSTER_BY_NAME.get(canonical_member(name))
    if not member or member["control"] != "room":
        return None
    return {"model": member["model"], "effort": member["effort"]}
