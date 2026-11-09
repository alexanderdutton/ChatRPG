"""Knowledge as a mechanic (slice 6, §23).

Pure functions over the player's learned-knowledge ledger (a list of
knowledge-entry snapshots with provenance, kept in the session row)
and an NPC's knowledge set (world state). No database access here;
the GameStateManager owns storage, this module owns the rules.

The rules:

- Matching is by topic string, not semantics. An entry matches a
  check when its topic equals the check's subject (normalized), or
  either one appears inside the other — so a subject of "Corvin the
  poacher" matches a topic of "Corvin". Without a subject, ledger
  topics are substring-matched against the check's description.
- `applies_to` scopes an entry to one challenge type; None means any
  type about the topic.
- Modifiers sum. Only non-zero modifiers are returned, so the
  metadata shows exactly what moved the DC and by how much.
- Learning is deduped by entry id: the player cannot learn the same
  fact twice.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_word(haystack: str, needle: str) -> bool:
    """Whole-word match: `needle` must appear as a word (or phrase)
    in `haystack`. A topic like "Oakhaven" no longer fires on any
    description merely containing those letters (Ellie review,
    finding 4)."""
    if not needle:
        return False
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


def _topic_matches(topic: str, subject: Optional[str]) -> bool:
    """Normalized topic/subject match: equal, or either one appearing
    as a whole word (or phrase) inside the other."""
    t, s = _normalize(topic), _normalize(subject)
    if not t or not s:
        return False
    return _contains_word(s, t) or _contains_word(t, s)


def knowledge_modifiers(
    ledger: List[Dict[str, Any]],
    subjects: Optional[List[str]] = None,
    check_type: Optional[str] = None,
    description: str = "",
) -> List[int]:
    """The DC modifiers the player's learned knowledge contributes to a
    check about `subjects` (or, without any, described by `description`).

    Each matching ledger entry contributes its `dc_modifier` when its
    `applies_to` is None or equals the check type. An entry matches
    once, even if several subjects would match it.
    """
    modifiers: List[int] = []
    subjects = [s for s in (subjects or []) if s]
    for entry in ledger or []:
        if not entry.get("dc_modifier"):
            continue
        applies_to = entry.get("applies_to")
        if applies_to is not None:
            if check_type is None:
                # Scoped knowledge with nothing to scope it against:
                # do not match, and say so (Ellie review, finding 5).
                # A future caller that forgets the check type gets no
                # modifier instead of an unscoped one.
                logger.warning(
                    f"Knowledge entry {entry.get('id')!r} applies to "
                    f"{applies_to!r} but the check has no type; skipping.")
                continue
            if _normalize(applies_to) != _normalize(check_type):
                continue
        topic = entry.get("topic", "")
        matched = any(_topic_matches(topic, s) for s in subjects)
        if not matched and not subjects and description:
            matched = _contains_word(_normalize(description),
                                     _normalize(topic))
        if matched:
            modifiers.append(int(entry["dc_modifier"]))
    return modifiers


def validate_shared(
    shared_ids: List[str],
    npc_knowledge: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split a turn's `knowledge_shared` ids into the entries the NPC
    actually knows and the unknown ids (dropped and logged by the
    caller). The model claiming to share something the NPC does not
    know is itself a signal."""
    by_id = {e.get("id"): e for e in npc_knowledge or []
             if e.get("id") is not None}
    valid: List[Dict[str, Any]] = []
    unknown: List[str] = []
    for shared_id in shared_ids or []:
        if shared_id is None:
            # An entry without an id collapses into a None key (Ellie
            # review, finding 6); skip it rather than matching it.
            continue
        entry = by_id.get(shared_id)
        if entry is None:
            unknown.append(shared_id)
        else:
            valid.append(entry)
    return valid, unknown


def learn_knowledge(
    ledger: List[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    learned_from: str,
    turn_number: int,
    session_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Add `entries` to the player's ledger, each snapshotted with its
    provenance. Returns the updated ledger and the newly learned
    entries (already-known ids are skipped, not duplicated)."""
    known_ids = {e.get("id") for e in ledger or []}
    updated = list(ledger or [])
    newly: List[Dict[str, Any]] = []
    for entry in entries or []:
        if entry.get("id") in known_ids:
            continue
        snapshot = dict(entry)
        snapshot["learned_from"] = learned_from
        snapshot["turn"] = turn_number
        snapshot["session_id"] = session_id
        updated.append(snapshot)
        newly.append(snapshot)
        known_ids.add(entry.get("id"))
    return updated, newly
