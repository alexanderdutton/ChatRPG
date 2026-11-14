"""Knowledge as a mechanic (slice 6, §23; gossip with reliability,
slice 7, §24).

Pure functions over the player's learned-knowledge ledger (a list of
knowledge-entry snapshots with provenance, kept in the session row),
an NPC's knowledge set (world state), and the session's rumor records
(what NPCs said that may be wrong, with the engine's verdict on each).
No database access here; the GameStateManager owns storage, this
module owns the rules.

The rules:

- Matching is by topic string, not semantics. An entry matches a
  check when its topic equals the check's subject (normalized), or
  either one appears inside the other — so a subject of "Corvin the
  poacher" matches a topic of "Corvin". Without a subject, ledger
  topics are substring-matched against the check's description.
- `applies_to` scopes an entry to one challenge type; None means any
  type about the topic.
- Modifiers sum, and scale with the entry's confidence: high applies
  the full modifier, medium half (rounded toward zero), low none.
  A low-confidence fact is still learned and recorded — the player
  heard it, they just cannot rely on it. Only non-zero applied
  modifiers are returned, so the metadata shows exactly what moved
  the DC and by how much; the breakdown shows the rest, including
  the entries that contributed nothing and why.
- Learning is deduped by entry id: the player cannot learn the same
  fact twice.
- Rumors are recorded, never corrected in dialogue: a rumor claim
  the engine can check gets a verdict (true / false / unknown) and a
  record; the dialogue stands even when the verdict is false —
  diegetic fallibility. The record flips only when the player learns
  a truth-grade fact (source "saw", confidence "high") on the same
  topic: false -> corrected, true -> confirmed. Unknown rumors never
  flip on a topic match alone — the engine never knew, and a topic
  match does not prove the specific claim. Past rolls stand:
  reliability affects future modifiers only.
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


def _scale_modifier(dc_modifier: int, confidence: Optional[str]) -> int:
    """Reliability scaling (§24): how much of a knowledge entry's
    listed modifier actually applies to a check. High confidence
    applies it in full; medium halves it, rounded toward zero
    (-5 -> -2, -1 -> 0); low applies nothing. An unknown confidence
    is treated as medium — the entry says nothing about its own
    reliability, so the engine trusts it halfway."""
    if confidence == "high":
        return int(dc_modifier)
    if confidence == "low":
        return 0
    # Medium, and anything unrecognized: half, toward zero.
    return int(int(dc_modifier) / 2)


def _scale_reason(confidence: Optional[str]) -> str:
    if confidence == "high":
        return "high confidence — full modifier"
    if confidence == "low":
        return "low confidence — no help"
    return "medium confidence — halved"


def _matching_entries(
    ledger: List[Dict[str, Any]],
    subjects: Optional[List[str]] = None,
    check_type: Optional[str] = None,
    description: str = "",
) -> List[Dict[str, Any]]:
    """Ledger entries that match this check — the shared matching
    rule behind both the applied modifiers and the breakdown, so the
    two can never disagree about what matched."""
    subjects = [s for s in (subjects or []) if s]
    matched: List[Dict[str, Any]] = []
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
        hit = any(_topic_matches(topic, s) for s in subjects)
        if not hit and not subjects and description:
            hit = _contains_word(_normalize(description),
                                 _normalize(topic))
        if hit:
            matched.append(entry)
    return matched


def knowledge_modifiers(
    ledger: List[Dict[str, Any]],
    subjects: Optional[List[str]] = None,
    check_type: Optional[str] = None,
    description: str = "",
) -> List[int]:
    """The DC modifiers the player's learned knowledge contributes to a
    check about `subjects` (or, without any, described by `description`).

    Each matching ledger entry contributes its `dc_modifier`, scaled
    by its confidence (§24): high in full, medium halved, low nothing.
    An entry matches once, even if several subjects would match it.
    Only non-zero applied modifiers are returned.
    """
    return [applied for applied in
            (_scale_modifier(int(e["dc_modifier"]), e.get("confidence"))
             for e in _matching_entries(ledger, subjects, check_type,
                                       description))
            if applied != 0]


def knowledge_modifier_breakdown(
    ledger: List[Dict[str, Any]],
    subjects: Optional[List[str]] = None,
    check_type: Optional[str] = None,
    description: str = "",
) -> List[Dict[str, Any]]:
    """Per-entry detail for the same check: id, topic, confidence,
    source, the listed modifier, the applied modifier after
    reliability scaling, and why. Entries that applied nothing are
    included — a low-confidence fact the player heard but cannot rely
    on is exactly what the player needs to see."""
    out: List[Dict[str, Any]] = []
    for entry in _matching_entries(ledger, subjects, check_type,
                                   description):
        listed = int(entry["dc_modifier"])
        out.append({
            "id": entry.get("id"),
            "topic": entry.get("topic"),
            "confidence": entry.get("confidence"),
            "source": entry.get("source"),
            "listed_modifier": listed,
            "applied_modifier": _scale_modifier(
                listed, entry.get("confidence")),
            "reason": _scale_reason(entry.get("confidence")),
        })
    return out


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


def record_rumors(validation: Any, npc_name: str,
                  turn_number: int) -> List[Dict[str, Any]]:
    """Rumor records for one validated turn (§24): every claim the
    model hedged as a rumor, with the engine's verdict on it.

    - contradicts -> false: the engine knows the claim is wrong
      (character-location or exit check fired). The dialogue stands
      — no correction note, that is diegetic fallibility — but the
      record remembers it was false.
    - grounded -> true: the rumor is true as far as the engine can
      check.
    - anything else (new_proposed / ungrounded / unverifiable) ->
      unknown: the engine cannot grade this one, and says so.

    `validation` is a TurnValidation; the function reads only its
    claims, so it stays a pure function of the checked turn."""
    records: List[Dict[str, Any]] = []
    for claim in validation.claims:
        if claim.kind != "rumor" or not claim.subject:
            # A rumor with no subject asserts nothing checkable, and
            # nothing worth remembering.
            continue
        if claim.verdict == "contradicts":
            verdict = "false"
        elif claim.verdict == "grounded":
            verdict = "true"
        else:
            verdict = "unknown"
        records.append({
            "subject": claim.subject,
            "text": claim.text,
            "said_by": npc_name,
            "turn": turn_number,
            "verdict": verdict,
            "status": "open",
        })
    return records


def add_rumor_records(
    records: List[Dict[str, Any]],
    new_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Add `new_records` to the session's rumor records, deduped by
    (said_by, subject, text) — the same NPC repeating the same rumor
    does not stack records. Returns the updated list and the newly
    added records."""
    known = {(r.get("said_by"), _normalize(r.get("subject") or ""),
              _normalize(r.get("text") or "")) for r in records or []}
    updated = list(records or [])
    newly: List[Dict[str, Any]] = []
    for record in new_records or []:
        key = (record.get("said_by"),
               _normalize(record.get("subject") or ""),
               _normalize(record.get("text") or ""))
        if key in known:
            continue
        updated.append(dict(record))
        newly.append(dict(record))
        known.add(key)
    return updated, newly


def reconcile_rumors(
    records: List[Dict[str, Any]],
    newly_learned: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Truth discovery (§24): when the player learns a truth-grade
    fact — source "saw", confidence "high" — on a topic matching an
    open rumor record, the record's status flips: a false rumor is
    "corrected", a true one "confirmed". Unknown rumors never flip
    on a topic match alone. Returns the updated records and the
    flips, each naming the entry that corrected or confirmed it.
    A record flips at most once: the first truth-grade learn on its
    topic settles it."""
    truth_grade = [e for e in newly_learned or []
                   if e.get("source") == "saw"
                   and e.get("confidence") == "high"]
    if not truth_grade:
        return list(records or []), []
    updated = [dict(r) for r in records or []]
    flips: List[Dict[str, Any]] = []
    for record in updated:
        if record.get("status") != "open":
            continue
        verdict = record.get("verdict")
        if verdict not in ("false", "true"):
            continue
        match = next(
            (e for e in truth_grade
             if _topic_matches(e.get("topic", ""),
                               record.get("subject"))),
            None)
        if match is None:
            continue
        record["status"] = ("corrected" if verdict == "false"
                            else "confirmed")
        record["settled_by"] = match.get("id")
        record["settled_turn"] = match.get("turn")
        flips.append({
            "subject": record.get("subject"),
            "text": record.get("text"),
            "said_by": record.get("said_by"),
            "old_status": "open",
            "new_status": record["status"],
            "settled_by": match.get("id"),
        })
    return updated, flips
