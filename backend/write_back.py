"""Write-back / commit bridge (slice 5, §22).

The validation invariant (slice 4) records proposed names and never
commits them. This module is the other half: it takes the structured
`turn.new_entities` the model proposed this turn and commits the ones
that are safe to commit into the loaded world — canon with provenance —
and persists the world file.

Commit rules (§22):

- Only `character` and `location` kinds are committed. `item` / `other`
  have no world model yet, so they are logged as uncommitted proposals
  with the reason.
- A name that already exists (exact normalized match) is skipped as a
  duplicate; a near match (shared word, or a word appearing inside
  another word) is skipped as a possible duplicate. Conservative on
  purpose: the engine would rather skip than fork an identity.
- Every committed entity gets a stable slug id and a provenance tag
  (source, session, turn, NPC, player-mentioned vs model-invented, and
  which fields the engine filled with defaults).
- The write goes through `game_world.add_location` / `add_character`,
  never direct dict assignment, and persists through `save_world()`,
  which writes atomically and filters out the debug NPC. If the save
  fails, the turn does not crash: the in-memory commit stands for the
  session and the result flags `persistence_failed`.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from .game_world import game_world, save_world
from .models import Character, Location, NewEntity, TurnResponse

logger = logging.getLogger(__name__)

DEBUG_NPC_NAME = "The Architect"
_ARTICLES = {"a", "an", "the", "of", "and", "or"}
_STUB_LAYOUT = [".....", ".....", ".....", ".....", "....."]


def slugify(name: str) -> str:
    """A stable id from a name: lowercased, non-alphanumerics to dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "entity"


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _tokens(name: str) -> set:
    return {
        t for t in re.split(r"[^a-z0-9]+", _normalize(name))
        if t and t not in _ARTICLES and len(t) >= 3
    }


def _is_near_duplicate(name: str, existing: str) -> bool:
    """Heuristic identity check: a shared whole word, or a word of one
    name appearing inside a word of the other (so 'Bram the smith'
    trips against 'the blacksmith' via 'smith'). Short words are
    ignored so 'Jo' does not trip against 'Joren'."""
    new_tokens = _tokens(name)
    old_tokens = _tokens(existing)
    if new_tokens & old_tokens:
        return True
    for a in new_tokens:
        for b in old_tokens:
            if a in b or b in a:
                return True
    return False


def _unique_id(name: str, taken: set) -> str:
    base = slugify(name)
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _first_sentence(description: str) -> str:
    text = description.strip()
    if "." in text:
        return text.split(".", 1)[0].strip() + "."
    return text[:120]


class WriteBackResult:
    """The outcome of one turn's commit, for the response metadata."""

    def __init__(self):
        self.committed: List[Dict[str, Any]] = []
        self.skipped: List[Dict[str, str]] = []
        self.uncommitted: List[Dict[str, str]] = []
        self.persistence_failed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "committed": self.committed,
            "skipped": self.skipped,
            "uncommitted": self.uncommitted,
            "persistence_failed": self.persistence_failed,
        }

    def summary(self) -> str:
        return (
            f"{len(self.committed)} committed, "
            f"{len(self.skipped)} skipped, "
            f"{len(self.uncommitted)} uncommitted"
            + ("; persistence FAILED" if self.persistence_failed else "")
        )


def commit_new_entities(
    turn: TurnResponse,
    session_id: str,
    npc_name: str,
    player_message: str = "",
    current_location: Optional[str] = None,
    npc_x: int = 0,
    npc_y: int = 0,
    turn_number: int = 0,
    world_file: Optional[str] = None,
) -> WriteBackResult:
    """Commit `turn.new_entities` into the loaded world, canon with
    provenance, and persist the world file. Runs once per turn, after
    claims validation, so a proposal can never ground its own claims."""
    result = WriteBackResult()
    if not turn.new_entities:
        return result

    player_text = _normalize(player_message)

    existing_names = (
        [loc for loc in game_world.locations]
        + [name for chars in game_world.characters.values()
           for name in chars]
    )
    existing_ids = {
        entity.id for loc in game_world.locations.values()
        for entity in [loc, *loc.characters]
        if entity.id
    }
    taken_ids = existing_ids | {slugify(n) for n in existing_names}

    for entity in turn.new_entities:
        if entity.kind not in ("character", "location"):
            result.uncommitted.append({
                "name": entity.name,
                "kind": entity.kind,
                "reason": f"No world model for kind {entity.kind!r} yet.",
            })
            logger.info(
                f"Write-back: {entity.name!r} ({entity.kind}) not "
                "committed — no world model for that kind.")
            continue

        normalized = _normalize(entity.name)
        duplicate = next(
            (n for n in existing_names if _normalize(n) == normalized),
            None,
        )
        if duplicate is not None:
            result.skipped.append({
                "name": entity.name,
                "reason": f"Duplicate of existing {duplicate!r}.",
            })
            logger.warning(
                f"Write-back: {entity.name!r} skipped — duplicate of "
                f"{duplicate!r}.")
            continue

        near = next(
            (n for n in existing_names if _is_near_duplicate(entity.name, n)),
            None,
        )
        if near is not None:
            result.skipped.append({
                "name": entity.name,
                "reason": f"Possible duplicate of existing {near!r}.",
            })
            logger.warning(
                f"Write-back: {entity.name!r} skipped — possible "
                f"duplicate of {near!r}.")
            continue

        origin = (
            "player-mentioned"
            if normalized and normalized in player_text
            else "model-invented"
        )
        # The model may omit the description; the engine fills a plain
        # one and says so, rather than crashing on None.
        description = entity.description or (
            f"A {entity.kind} named {entity.name}.")
        description_defaulted = entity.description is None
        provenance: Dict[str, Any] = {
            "source": "generated",
            "session_id": session_id,
            "turn": turn_number,
            "npc": npc_name,
            "origin": origin,
            "engine_defaults": [],
        }

        if entity.kind == "character":
            place = entity.location or current_location
            if not place or not game_world.get_location(place):
                # The model wanted them somewhere the world does not
                # have yet (e.g. the ungenerated Whispering Woods):
                # record the request and fall back to the current
                # location rather than dropping the character.
                if entity.location:
                    provenance["requested_location"] = entity.location
                place = current_location
            if not place or not game_world.get_location(place):
                result.uncommitted.append({
                    "name": entity.name,
                    "kind": entity.kind,
                    "reason": "No location to place the character in.",
                })
                logger.warning(
                    f"Write-back: {entity.name!r} not committed — no "
                    "location to place them in.")
                continue
            provenance["engine_defaults"] = [
                "race", "occupation", "personality_prompt", "x", "y",
            ]
            if description_defaulted:
                provenance["engine_defaults"].append("description")
            character = Character(
                id=_unique_id(entity.name, taken_ids),
                name=entity.name,
                race="unknown",
                occupation="unknown",
                description=description,
                personality_prompt=(
                    f"You are {entity.name}. {description}"
                ),
                resource_level="poor",
                x=npc_x,
                y=npc_y,
                short_description=_first_sentence(description),
                provenance=provenance,
            )
            game_world.add_character(character, place)
            taken_ids.add(character.id)
            existing_names.append(entity.name)
            result.committed.append({
                "kind": "character",
                "name": entity.name,
                "id": character.id,
                "location": place,
            })
            logger.info(
                f"Write-back: committed character {entity.name!r} "
                f"(id {character.id}) at {place} "
                f"[{origin}, turn {turn_number}].")
        else:  # location — committed as a stub; see §22
            provenance["engine_defaults"] = [
                "raw_layout", "map_key", "player_initial_location",
                "exits",
            ]
            if description_defaulted:
                provenance["engine_defaults"].append("description")
            if entity.location and game_world.get_location(entity.location):
                provenance["proposed_connection"] = entity.location
            location = Location(
                id=_unique_id(entity.name, taken_ids),
                name=entity.name,
                description=description,
                raw_layout=list(_STUB_LAYOUT),
                map_key={},
                player_initial_location={"x": 0, "y": 0},
                exits={},
                features=[],
                characters=[],
                provenance=provenance,
            )
            game_world.add_location(location)
            taken_ids.add(location.id)
            existing_names.append(entity.name)
            result.committed.append({
                "kind": "location",
                "name": entity.name,
                "id": location.id,
                "location": None,
            })
            logger.info(
                f"Write-back: committed location {entity.name!r} "
                f"(id {location.id}, stub) [{origin}, "
                f"turn {turn_number}].")

    if result.committed:
        try:
            save_world(world_file)
        except Exception:
            result.persistence_failed = True
            logger.exception(
                "Write-back: world save failed; committed entities "
                "stay in memory for this session only.")

    return result
