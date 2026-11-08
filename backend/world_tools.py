"""Read-only world tools for the tool-call turn (slice 2 implementation).

The model may call these when a conversation needs a fact that is not
in its bindings. Tools read; the turn schema writes — writes stay in
the structured turn output, propose-validate-commit. The tools answer
from the LOADED world (game_world) and the session's current location,
so what they report is exactly what the engine knows: a place or person
that exists nowhere in the loaded world is a miss, and an exit
destination that has not been generated yet is reported as named but
ungenerated, not invented.

Matching follows the prototype rig (toolbox.py): a query hits an entity
when every query token (stopwords out) appears inside that entity's
name; when only some tokens match, the entity is a near match, not a
hit — so "Corvin's Mill" misses while returning both Corvin and the
mill as near matches, instead of wrongly matching either one.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .game_world import game_world

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_TURN = 2

TOOLS_BLOCK = """
**WORLD TOOLS — use them when you need a fact.**

You have three read-only tools. They are the only way to learn about
a place or person you cannot see from where you stand. Use them when
the conversation needs a fact that is not in your bindings above.

- `locate_entity(name)` — where a named person, place, or thing
  actually is: its location, coordinates, and a one-line description.
  If it does not exist, the tool says so, and lists anything similar.
- `describe_here()` — the place you are standing in right now: its
  description, features, exits, and who is present.
- `list_nearby()` — who is here with you, and where each exit leads.

**How to call one:** reply with a fenced JSON block and nothing else:
```json
{"tool_calls": [{"tool": "describe_here", "args": {}}]}
```
Then STOP. The results come back to you, and you continue. You may
make at most **2 tool calls in total** in one turn.

**The rule that matters:** in your dialogue, name only places and
people that come from your bindings above, or from a tool result this
turn. If a tool says something does not exist, do not invent it:
ask the player what they mean, or say honestly that you do not know.

When you have everything you need — or when you have decided you
need no tool — reply with your final TurnResponse JSON, exactly as
the turn format above requires.
"""

_STOPWORDS = ("the", "a", "an")

_manager = None


def _gsm():
    # Deferred import: game_state_manager imports gemini_service, which
    # imports this module — a top-level import here would be circular.
    # By the time a tool runs, both modules are fully loaded.
    global _manager
    if _manager is None:
        from .game_state_manager import GameStateManager
        _manager = GameStateManager()
    return _manager


def _tokens(text: str) -> List[str]:
    text = text.lower().replace("'", "")
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _token_match(query_token: str, name_tokens: List[str]) -> bool:
    """True when the query token appears inside some token of the name."""
    return any(query_token in nt or nt in query_token for nt in name_tokens)


def _all_entities() -> List[Dict[str, Any]]:
    """Every searchable thing in the loaded world, plus exit destinations
    that are named but not yet generated (real world state — the exit
    exists — just without a description or anyone in it yet)."""
    out: List[Dict[str, Any]] = []
    seen_locations = set()
    for loc in game_world.locations.values():
        seen_locations.add(loc.name.lower())
        out.append({"kind": "location", "name": loc.name, "record": loc})
        for ch in loc.characters:
            out.append({"kind": "character", "name": ch.name, "record": ch,
                        "location": loc.name})
        for feat in (loc.features or []):
            out.append({"kind": "feature", "name": feat.name, "record": feat,
                        "location": loc.name})
    # Named exit destinations with no loaded location behind them.
    for loc in game_world.locations.values():
        for dest in (loc.exits or {}).values():
            if dest and dest.lower() not in seen_locations:
                out.append({"kind": "location", "name": dest, "record": None,
                            "location": None, "ungenerated": True})
    return out


def _summary(kind: str, name: str, entity: Dict[str, Any]) -> Dict[str, Any]:
    rec = entity.get("record")
    if kind == "character":
        where = entity.get("location", "")
        if rec is not None and getattr(rec, "x", None) is not None:
            where += f" at [{rec.x}, {rec.y}]"
        return {"kind": kind, "name": name, "where": where,
                "occupation": getattr(rec, "occupation", "") or "",
                "description": getattr(rec, "description", "") or ""}
    if kind == "location":
        if entity.get("ungenerated") or rec is None:
            return {"kind": kind, "name": name,
                    "where": "named as an exit destination, but not yet generated",
                    "description": "No description yet — this place has not been generated."}
        exits = getattr(rec, "exits", {}) or {}
        return {"kind": kind, "name": name,
                "where": ("exits: " + ", ".join(f"{d} to {dest}" for d, dest in exits.items() if dest))
                if any(exits.values()) else "no exits",
                "description": getattr(rec, "description", "") or ""}
    return {"kind": kind, "name": name,
            "where": entity.get("location", ""),
            "description": getattr(rec, "description", "") or ""}


def locate_entity(name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Where a named person, place, or thing actually is — or a miss with
    near matches. `session_id` is accepted for a uniform tool signature;
    locating does not depend on where the player stands."""
    query_tokens = [t for t in _tokens(name) if t not in _STOPWORDS]
    hits: List[Dict[str, Any]] = []
    near: List[Dict[str, Any]] = []
    for entity in _all_entities():
        name_tokens = _tokens(entity["name"])
        matched = [qt for qt in query_tokens if _token_match(qt, name_tokens)]
        if query_tokens and len(matched) == len(query_tokens):
            hits.append(entity)
        elif matched:
            near.append(_summary(entity["kind"], entity["name"], entity))
    if hits:
        entity = hits[0]
        out = {"found": True}
        out.update(_summary(entity["kind"], entity["name"], entity))
        return out
    if near:
        return {"found": False, "query": name,
                "message": f"No place or person called '{name}' exists.",
                "near_matches": near}
    return {"found": False, "query": name,
            "message": f"No place or person called '{name}' exists, and nothing similar.",
            "near_matches": []}


def describe_here(session_id: str) -> Dict[str, Any]:
    """The place the player and the NPC are standing in right now."""
    current = _gsm().get_current_location_name(session_id)
    loc = game_world.get_location(current)
    if loc is None:
        return {"error": f"No location loaded named {current!r}."}
    chars = []
    for ch in loc.characters:
        entry = f"{ch.name} ({getattr(ch, 'occupation', '')})"
        if getattr(ch, "x", None) is not None:
            entry += f" at [{ch.x}, {ch.y}]"
        chars.append(entry)
    return {"location": loc.name,
            "description": loc.description,
            "features": [f.name for f in (loc.features or [])],
            "exits": {d: dest for d, dest in (loc.exits or {}).items() if dest},
            "characters_present": chars}


def list_nearby(session_id: str) -> Dict[str, Any]:
    """Who is here, and where each exit leads (generated or not yet)."""
    current = _gsm().get_current_location_name(session_id)
    loc = game_world.get_location(current)
    if loc is None:
        return {"error": f"No location loaded named {current!r}."}
    destinations = []
    for direction, dest in (loc.exits or {}).items():
        if not dest:
            continue
        status = ("not yet generated" if game_world.get_location(dest) is None
                  else "exists")
        destinations.append(f"{direction} to {dest} ({status})")
    return {"here": loc.name,
            "characters_present": [
                f"{ch.name} ({getattr(ch, 'occupation', '')})"
                for ch in loc.characters],
            "exit_destinations": destinations}


def execute_tool_call(session_id: Optional[str], tool: Optional[str],
                      args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one tool call and return its JSON result. Unknown tools and
    bad arguments return an error result instead of raising, so the
    model sees the failure and can correct it."""
    args = args or {}
    try:
        if tool == "locate_entity":
            if not args.get("name"):
                return {"error": "locate_entity needs a 'name' argument."}
            return locate_entity(str(args["name"]), session_id)
        if tool == "describe_here":
            if session_id is None:
                return {"error": "describe_here needs a session."}
            return describe_here(session_id)
        if tool == "list_nearby":
            if session_id is None:
                return {"error": "list_nearby needs a session."}
            return list_nearby(session_id)
        return {"error": f"Unknown tool {tool!r}. Available tools: "
                         "locate_entity, describe_here, list_nearby."}
    except Exception as e:  # never let a tool failure break the turn
        logger.warning(f"Tool {tool!r} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool {tool!r} failed: {e}"}


def parse_tool_calls(text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse a tool-calls reply: a fenced ```json block or a raw JSON
    object with a "tool_calls" key. Returns the call list, or None when
    the reply is not a tool call (i.e. it is the final TurnResponse)."""
    if not text:
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        raw = re.search(r"(\{.*\})", candidate, re.DOTALL)
        if raw:
            candidate = raw.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    calls = parsed.get("tool_calls") if isinstance(parsed, dict) else None
    if not isinstance(calls, list):
        return None
    return [{"tool": c.get("tool"), "args": c.get("args") or {}}
            for c in calls if isinstance(c, dict)]
