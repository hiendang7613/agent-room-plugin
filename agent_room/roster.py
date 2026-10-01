"""The room's members as data: who they are, which host runs them, who is the admin gateway, and the intended model.

The runtime still launches every member with its host's own model and effort: `model` and `effort` stay None
(inherit) until a launch step applies them. `intended` records the admin's chosen defaults (all xhigh) so that step
and the documentation read one source. Stable ids stay the keys of the ledger; an alias is another name for the same
member (the admin's CLAUDE_WORKER and CODEX_WORKER). Exactly one member is the gateway: the only one that talks to
the admin and records admin intent. A Codex-hosted gateway needs its own prompt-provenance evidence and is not enabled
here.
"""

ROSTER = (
    {"name": "CLAUDE_01", "alias": "CLAUDE_WORKER", "host": "claude", "role": "worker", "gateway": True, "default_mode": True,
     "model": None, "effort": None, "intended": {"model": "Sonnet 5.5", "effort": "xhigh"}},
    {"name": "CODEX_01", "alias": "CODEX_WORKER", "host": "codex", "role": "worker", "gateway": False, "default_mode": True,
     "model": None, "effort": None, "intended": {"model": "Luna 6", "effort": "xhigh"}},
    {"name": "CLAUDE_EXPERT", "alias": None, "host": "claude", "role": "expert", "gateway": False, "default_mode": True,
     "model": None, "effort": None, "intended": {"model": "Opus 5.5", "effort": "xhigh"}},
    {"name": "CODEX_EXPERT", "alias": None, "host": "codex", "role": "expert", "gateway": False, "default_mode": True,
     "model": None, "effort": None, "intended": {"model": "Sol 6.1", "effort": "xhigh"}},
)

MEMBERS = tuple(member["name"] for member in ROSTER)
DEFAULT_MEMBERS = tuple(member["name"] for member in ROSTER if member["default_mode"])
GATEWAY = next(member["name"] for member in ROSTER if member["gateway"])
ALIASES = {member["alias"]: member["name"] for member in ROSTER if member["alias"]}
LAUNCHED_CLAUDE = next(member["name"] for member in ROSTER if member["host"] == "claude" and not member["gateway"])


def canonical_member(name):
    """The stable id for a member id or alias; other text (including an empty name) is returned unchanged."""
    return ALIASES.get(name, name)
