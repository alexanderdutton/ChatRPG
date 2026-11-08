"""Validation invariant (slice 4): named references must come from
bindings or tool results this turn.

Design note §13/§16/§20. The engine checks what the model asserted
against the built world, at the one place a statement is compared
against world state. Two channels:

- the `claims` field (primary): the model's own declaration of what
  it asserted, each with a subject and a hedge (fact / inference /
  rumor);
- a dialogue scan (cross-check): known world names appearing in the
  dialogue that the model neither grounded nor declared.

What "grounded this turn" means: the name was shown to the model by
the engine this turn — bindings (current location, its features, exits
and destinations, characters present, the NPC itself), the NPC's memory
and quest log as passed in the prompt, or a tool result this turn (a
locate hit, a near match, or a name the model looked up — including a
miss, which grounds a *negative* claim about that name).

Verdicts per claim:
- grounded: subject shown this turn, nothing contradicting.
- verified_absent: subject looked up this turn and the tool missed —
  the negative is established by the tool (§18 finding 2), and the
  verdict carries the tool call as provenance.
- new_proposed: subject names nothing in the world and was shown
  nowhere — a proposed addition, recorded, NOT committed (write-back
  is a later slice).
- contradicts: subject is a known character and the claim text asserts
  it at a different known place, or asserts an exit leads somewhere
  it does not.
- ungrounded: subject is a real world entity the model was not shown
  this turn (latent knowledge; the fact may be true, the grounding
  is not).
- unverifiable: no checkable subject, or the assertion is not
  checkable against world state (opinions, moods, uncheckable facts).

Mismatch policy (one table, MISMATCH_POLICY, so it flips in one
place — the design leaves the choice per claim kind, §13). Only the
validator reads this table. The prompt stays policy-agnostic: it
carries the naming rule (name only bindings/tool-result places and
people), not the consequences — the engine decides those here.
- a hard-fact contradiction (fact/inference about where someone is,
  where an exit leads, who is present) is CORRECTED: the false claim
  is dropped from the record and a [System] correction note goes into
  the conversation history, so the NPC can self-correct in character
  next turn. No re-prompt — the dialogue already went out, and
  economy says the correction lands in the next turn's context, not
  a new call.
- a rumor contradiction is ALLOWED to be wrong: recorded as a rumor
  with provenance. Gossip and history may be wrong; that is the design.
- a new name is PROPOSED, never committed here.
- an ungrounded mention is NOTED, not corrected — the fact may be true.

Honest limits, stated plainly: a wholly invented name can only be
caught if the model declares it (claims / new_entities / referrals);
the dialogue scan catches known names, not inventions. The location
contradiction check is bounded — it reads the place names in the
claim text, not the sentence's meaning, so an origin ("came from
Riverford") or a proximity ("camps near Riverford") does not count
as asserting the subject is *at* that place, and a claim that
mentions the subject's actual location alongside another place is
treated as consistent. Free text cannot be fully verified; the
engine now knows what it could not check, and the claim carries its
hedge with it.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .game_world import game_world
from .models import TurnResponse

logger = logging.getLogger(__name__)

# Mismatch policy: (verdict, kind) -> action. "correct" drops the false
# claim from the record and queues a [System] correction note; "allow_wrong"
# records a rumor as a rumor; "propose" records a new name without
# committing it; "note" logs an ungrounded mention; "accept" does nothing.
# Anything not listed here is accepted.
MISMATCH_POLICY = {
    ("contradicts", "fact"): "correct",
    ("contradicts", "inference"): "correct",
    ("contradicts", "rumor"): "allow_wrong",
    ("new_proposed", "fact"): "propose",
    ("new_proposed", "inference"): "propose",
    ("new_proposed", "rumor"): "propose",
    ("ungrounded", "fact"): "note",
    ("ungrounded", "inference"): "note",
    ("ungrounded", "rumor"): "note",
}

_DIRECTIONS = ("north", "south", "east", "west")
# Words that make a place mention an origin or a proximity, not a location.
_NON_LOCATIVE = ("from", "of", "near", "by", "around")
_ARTICLES = ("the ", "a ", "an ")


def _norm(name: str) -> str:
    """Lowercase, strip, drop a leading article, collapse whitespace."""
    out = (name or "").strip().lower()
    for art in _ARTICLES:
        if out.startswith(art):
            out = out[len(art):]
            break
    return re.sub(r"\s+", " ", out).strip()


def _looks_like_name(subject: str) -> bool:
    """True when the subject reads as a proper name, not a description.

    "Brom" and "Wizard's Tower" are names; "the blacksmith" is a
    description of one. A descriptive subject is a claims-field
    imprecision, not a world violation, so it is unverifiable rather
    than a proposed new entity.
    """
    text = (subject or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(lowered.startswith(art) for art in _ARTICLES):
        return False
    words = text.split()
    return all(w[:1].isupper() for w in words if w[:1].isalpha())


def _find_names(text: str, names: Dict[str, str]) -> List[str]:
    """Known names appearing in `text` (word-boundary, case-insensitive),
    in text order, as their normalized forms. `names` maps normalized
    name -> display name."""
    found: List[str] = []
    if not text:
        return found
    for norm in sorted(names, key=len, reverse=True):
        if re.search(r"\b" + re.escape(norm) + r"\b", text, re.IGNORECASE):
            found.append(norm)
    # Keep text order, not length order.
    found.sort(key=lambda n: re.search(
        r"\b" + re.escape(n) + r"\b", text, re.IGNORECASE).start())
    return found


def _locative_places(text: str, location_names: Dict[str, str]) -> List[str]:
    """Place names the text asserts the subject is *at* — a name
    appearing after from / out of / near / by / around is an origin or
    a proximity, not a location, and does not count."""
    places = _find_names(text, location_names)
    kept: List[str] = []
    for place in places:
        m = re.search(r"\b" + re.escape(place) + r"\b", text, re.IGNORECASE)
        before = text[:m.start()].rstrip()
        prev = before.split()[-1].lower().rstrip(".,;:") if before.split() else ""
        if prev in _NON_LOCATIVE:
            continue
        kept.append(place)
    return kept


@dataclass
class GroundedSet:
    """Everything the engine showed the model this turn, as names.

    `names` maps normalized name -> display name for every grounded
    name. `missed_lookups` maps a normalized looked-up name -> the
    query as asked, for names the tool reported as not existing.
    `character_locations` maps a normalized character name -> the
    display name of the location the world has them in.
    `location_names` maps normalized location name -> display name for
    every place the world knows (loaded locations plus named-but-
    ungenerated exit destinations). `known_names` is the same shape
    for every entity the world knows (locations, characters, features),
    used by the dialogue scan. `current_location` is the display name
    of the location the player and the NPC are standing in, and `exits`
    maps its directions to their destinations — the exit check reads
    them from here, so validation stays a pure function of the set.
    """
    names: Dict[str, str] = field(default_factory=dict)
    missed_lookups: Dict[str, str] = field(default_factory=dict)
    character_locations: Dict[str, str] = field(default_factory=dict)
    location_names: Dict[str, str] = field(default_factory=dict)
    known_names: Dict[str, str] = field(default_factory=dict)
    current_location: Optional[str] = None
    exits: Dict[str, str] = field(default_factory=dict)


def _world_indexes() -> Dict[str, Dict[str, str]]:
    """Closed name sets from the loaded world (no session needed)."""
    locations: Dict[str, str] = {}
    characters: Dict[str, str] = {}
    known: Dict[str, str] = {}
    for loc in game_world.locations.values():
        locations[_norm(loc.name)] = loc.name
        known[_norm(loc.name)] = loc.name
        for ch in loc.characters:
            characters[_norm(ch.name)] = loc.name
            known[_norm(ch.name)] = ch.name
        for feat in (loc.features or []):
            known[_norm(feat.name)] = feat.name
    # Named exit destinations with nothing behind them yet are real
    # world state — the exit exists — so they are known places too.
    for loc in game_world.locations.values():
        for dest in (loc.exits or {}).values():
            if dest and _norm(dest) not in locations:
                locations[_norm(dest)] = dest
                known[_norm(dest)] = dest
    return {"locations": locations, "characters": characters, "known": known}


def _collect_result_names(result: Any, names: Dict[str, str],
                         indexes: Dict[str, Dict[str, str]]) -> None:
    """Add every entity name a tool result surfaced to `names`.

    Structured "name" fields first (locate hit, near matches), then a
    scan of the result's JSON text for known world names — that covers
    describe_here / list_nearby, whose names live inside formatted
    strings.
    """
    if isinstance(result, dict):
        if isinstance(result.get("name"), str) and result["name"]:
            names[_norm(result["name"])] = result["name"]
        for value in result.values():
            _collect_result_names(value, names, indexes)
    elif isinstance(result, list):
        for value in result:
            _collect_result_names(value, names, indexes)
    elif isinstance(result, str):
        for norm in _find_names(result, indexes["known"]):
            names[norm] = indexes["known"][norm]


_manager = None


def _gsm():
    # Deferred import: game_state_manager imports gemini_service, which
    # imports world_tools, which already uses this same lazy pattern.
    # By the time validation runs, both modules are fully loaded.
    global _manager
    if _manager is None:
        from .game_state_manager import GameStateManager
        _manager = GameStateManager()
    return _manager


def build_prompt_grounded_set(session_id: str, npc_id: str) -> GroundedSet:
    """The prompt-time half of the grounded set: bindings, the NPC's
    memory, and the quest log as they stood when the system instruction
    was built — before the model answered. The tool-results half is
    merged afterwards by `add_tool_results`.

    The split matters: a quest the model offers this turn lists its own
    invented entities, and if the grounded set were built after the
    turn, those entities would ground the model's own claims about
    them — circular. Grounding is what the engine showed the model
    *before* it answered, plus what it fetched during the turn.
    """
    indexes = _world_indexes()
    grounded = GroundedSet(
        character_locations=indexes["characters"],
        location_names=indexes["locations"],
        known_names=indexes["known"])
    gsm = _gsm()

    # Bindings: the same data build_system_instruction puts in prose —
    # the NPC itself, the current location, its features, exits and
    # destinations, and the characters present.
    npc_info = gsm.get_npc_info(npc_id) or {}
    if npc_info.get("name"):
        grounded.names[_norm(npc_info["name"])] = npc_info["name"]
    current = gsm.get_current_location_name(session_id)
    if current:
        grounded.names[_norm(current)] = current
        grounded.current_location = current
    location_obj = game_world.get_location(current) if current else None
    if location_obj is not None:
        for feat in (location_obj.features or []):
            grounded.names[_norm(feat.name)] = feat.name
        for dest in (location_obj.exits or {}).values():
            if dest:
                grounded.names[_norm(dest)] = dest
        grounded.exits = {d: dest for d, dest in (location_obj.exits or {}).items()
                          if dest}
        for ch in location_obj.characters:
            grounded.names[_norm(ch.name)] = ch.name

    # The NPC's memory and quest log, as passed in the prompt: names
    # the model was shown there are grounded too.
    npc_state = gsm.get_npc_state(session_id, npc_id) or {}
    for note in (npc_state.get("memory") or []):
        for norm in _find_names(str(note), indexes["known"]):
            grounded.names[norm] = indexes["known"][norm]
    for quest in (gsm.get_quest_context_for_npc(session_id) or []):
        for entity in (quest.get("involved_entities") or []):
            grounded.names[_norm(str(entity))] = str(entity)
        for norm in _find_names(str(quest.get("description", "")),
                                indexes["known"]):
            grounded.names[norm] = indexes["known"][norm]

    return grounded


def add_tool_results(grounded: GroundedSet,
                     tool_calls_made: Optional[List[Dict[str, Any]]] = None
                     ) -> GroundedSet:
    """The tool-results half: merge what the model fetched this turn
    into the grounded set. A locate query names what the model asked
    about; a miss grounds a negative claim about that name, a hit or
    near match grounds the names the tool returned. Returns the same
    set, extended."""
    indexes = _world_indexes()
    for record in (tool_calls_made or []):
        args = record.get("args") or {}
        result = record.get("result") or {}
        if record.get("tool") == "locate_entity" and args.get("name"):
            query = str(args["name"])
            grounded.names[_norm(query)] = query
            if isinstance(result, dict) and result.get("found") is False:
                grounded.missed_lookups[_norm(query)] = query
        _collect_result_names(result, grounded.names, indexes)
    return grounded


def build_grounded_set(session_id: str, npc_id: str,
                       tool_calls_made: Optional[List[Dict[str, Any]]] = None
                       ) -> GroundedSet:
    """One-call form: prompt-time grounding plus tool results. Use
    this only where no turn output has landed between the prompt and
    the check — /interact builds the two halves separately, so a quest
    the model offers this turn cannot ground its own claims."""
    grounded = build_prompt_grounded_set(session_id, npc_id)
    return add_tool_results(grounded, tool_calls_made)


@dataclass
class ClaimValidation:
    """One claim, checked. `evidence` names what the verdict rests on
    (a binding, a tool call, the world state that contradicts it).
    `action` is the mismatch-policy outcome; `note` is the [System]
    correction note when the action is "correct", else None."""
    text: str
    subject: Optional[str]
    kind: str
    verdict: str
    action: str
    evidence: str = ""
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {"text": self.text, "subject": self.subject, "kind": self.kind,
               "verdict": self.verdict, "action": self.action,
               "evidence": self.evidence}
        if self.note:
            out["correction_note"] = self.note
        return out


@dataclass
class TurnValidation:
    """The whole turn, checked: one entry per claim, the dialogue
    cross-check, the correction notes to feed back, and the proposed
    additions (recorded, never committed here)."""
    claims: List[ClaimValidation] = field(default_factory=list)
    dialogue_mentions: List[Dict[str, str]] = field(default_factory=list)
    correction_notes: List[str] = field(default_factory=list)
    proposals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"claims": [c.to_dict() for c in self.claims],
                "dialogue_mentions": self.dialogue_mentions,
                "correction_notes": self.correction_notes,
                "proposals": self.proposals}

    def summary(self) -> str:
        counts: Dict[str, int] = {}
        for claim in self.claims:
            counts[claim.verdict] = counts.get(claim.verdict, 0) + 1
        parts = [f"{v}: {n}" for v, n in sorted(counts.items())]
        if self.dialogue_mentions:
            parts.append(f"ungrounded dialogue mentions: "
                         f"{len(self.dialogue_mentions)}")
        return ", ".join(parts) if parts else "no claims"


def _check_exit_claim(text: str, grounded: GroundedSet
                      ) -> Optional[ClaimValidation]:
    """A claim that an exit leads somewhere it does not — the one
    hard fact the design says an NPC may never be wrong about. Only
    fires when the text names both a direction and a destination."""
    m = re.search(
        r"\b(north|south|east|west)\b[^.,;]{0,40}?"
        r"\b(?:leads? to|goes to)\s+(?:the\s+)?([A-Z][A-Za-z' ]*?)"
        r"(?:[.,;]|$)",
        text or "")
    if not m:
        return None
    direction, dest = m.group(1).lower(), m.group(2).strip()
    dest_norm = _norm(dest)
    if dest_norm not in grounded.location_names:
        return None
    actual = (grounded.exits or {}).get(direction)
    if not actual:
        return None
    if _norm(actual) == dest_norm:
        return None
    current_name = grounded.current_location or "here"
    return ClaimValidation(
        text=text, subject=None, kind="fact", verdict="contradicts",
        action="correct",
        evidence=(f"The {direction} exit from {current_name} leads to "
                  f"{actual}, not {grounded.location_names[dest_norm]}."))


def validate_turn(turn: TurnResponse, grounded: GroundedSet,
                  npc_name: str = "the NPC") -> TurnValidation:
    """Check one structured turn against the grounded set and the
    loaded world. Pure function of (turn, grounded) — no session,
    no model, no writes."""
    result = TurnValidation()

    for claim in turn.claims:
        subject = (claim.subject or "").strip()
        subject_norm = _norm(subject)
        kind = claim.kind or "fact"

        # A claim with no named subject asserts nothing checkable.
        if not subject:
            result.claims.append(ClaimValidation(
                text=claim.text, subject=None, kind=kind,
                verdict="unverifiable", action="accept",
                evidence="No subject named."))
            continue

        # A negative established by a tool miss this turn: the tool
        # said the name does not exist, so the claim is grounded as
        # an absence, with the lookup as its provenance.
        if subject_norm in grounded.missed_lookups:
            result.claims.append(ClaimValidation(
                text=claim.text, subject=subject, kind=kind,
                verdict="verified_absent", action="accept",
                evidence=(f"Looked up this turn (locate_entity "
                          f"{grounded.missed_lookups[subject_norm]!r}); "
                          f"the tool reported no such place or person.")))
            continue

        # A descriptive subject ("the blacksmith") is not a name to
        # check; it is unverifiable, not a proposed new entity.
        if not _looks_like_name(subject):
            result.claims.append(ClaimValidation(
                text=claim.text, subject=subject, kind=kind,
                verdict="unverifiable", action="accept",
                evidence="Subject is a description, not a name."))
            continue

        # An exit claim is checked on its own terms — direction plus
        # destination — before the subject's grounding is considered.
        exit_check = _check_exit_claim(claim.text, grounded)
        if exit_check is not None:
            exit_check.subject = subject
            exit_check.kind = kind
            exit_check.action = MISMATCH_POLICY.get(
                ("contradicts", kind), "correct")
            if exit_check.action == "correct":
                exit_check.note = (
                    f"[System] Correction: {npc_name} said "
                    f"{claim.text!r}; {exit_check.evidence} "
                    f"{npc_name} was mistaken about this — if it comes "
                    f"up again, answer from the corrected fact.")
                result.correction_notes.append(exit_check.note)
            result.claims.append(exit_check)
            continue

        # A known character asserted at a different known place than
        # the world has them: a contradiction. The actual location
        # mentioned alongside is consistent, not a contradiction.
        actual = grounded.character_locations.get(subject_norm)
        if actual:
            places = _locative_places(claim.text, grounded.location_names)
            if places and _norm(actual) not in places:
                asserted = grounded.location_names[places[0]]
                action = MISMATCH_POLICY.get(("contradicts", kind), "accept")
                entry = ClaimValidation(
                    text=claim.text, subject=subject, kind=kind,
                    verdict="contradicts", action=action,
                    evidence=(f"{subject} is in {actual}; the claim "
                              f"puts them at {asserted}."))
                if action == "correct":
                    entry.note = (
                        f"[System] Correction: {npc_name} said "
                        f"{claim.text!r}; the world has {subject} in "
                        f"{actual}, not {asserted}. {npc_name} was "
                        f"mistaken about this — if it comes up again, "
                        f"answer from the corrected fact.")
                    result.correction_notes.append(entry.note)
                result.claims.append(entry)
                continue

        if subject_norm in grounded.names:
            result.claims.append(ClaimValidation(
                text=claim.text, subject=subject, kind=kind,
                verdict="grounded", action="accept",
                evidence="Subject shown to the model this turn."))
        elif subject_norm in grounded.known_names:
            action = MISMATCH_POLICY.get(("ungrounded", kind), "accept")
            result.claims.append(ClaimValidation(
                text=claim.text, subject=subject, kind=kind,
                verdict="ungrounded", action=action,
                evidence=(f"{subject} exists in the world but was not "
                          f"in this turn's bindings or tool results.")))
        else:
            action = MISMATCH_POLICY.get(("new_proposed", kind), "accept")
            result.claims.append(ClaimValidation(
                text=claim.text, subject=subject, kind=kind,
                verdict="new_proposed", action=action,
                evidence=(f"{subject} is in no world and was shown "
                          f"nowhere this turn.")))
            result.proposals.append(subject)

    # Dialogue cross-check: known names the dialogue names that are
    # neither grounded nor declared in claims / new entities / referrals.
    declared = {_norm(c.subject) for c in turn.claims if c.subject}
    declared |= {_norm(e.name) for e in turn.new_entities}
    declared |= {_norm(r.to_name) for r in turn.referrals}
    for norm in _find_names(turn.dialogue, grounded.known_names):
        if norm in grounded.names or norm in declared:
            continue
        display = grounded.known_names[norm]
        result.dialogue_mentions.append({
            "name": display,
            "verdict": "ungrounded",
            "evidence": (f"{display} appears in the dialogue but was "
                         f"not in this turn's bindings or tool results, "
                         f"and is not declared in claims, new entities, "
                         f"or referrals.")})

    return result
