"""The tick engine (section 25): simulation without the LLM.

Slice 8 is the clock only. The tick advances world time and, from
later slices, NPC agendas, rumor spread, standing, and scheduled
events. Everything here is pure functions on state: no model calls,
no I/O, deterministic -- the same seed plus the same inputs gives the
same world state, so the suite can assert exact world states after
N ticks.

The division of labor (section 16) stays intact: the simulation owns
state, the model owns behavior within state, the dice own uncertain
outcomes. The tick produces facts; the model narrates them.
"""
from typing import Any, Dict, List, Optional, Tuple

HOURS_PER_DAY = 24
START_DAY = 1
START_HOUR = 8

# Hour ranges for the time-of-day label the model sees. Night wraps
# midnight: 22, 23, 0, 1, 2, 3, 4.
_TIME_OF_DAY = (
    (5, 12, "morning"),
    (12, 18, "afternoon"),
    (18, 22, "evening"),
)


def new_clock() -> Dict[str, int]:
    """A fresh clock: Day 1, 08:00 -- morning in Oakhaven."""
    return {"day": START_DAY, "hour": START_HOUR}


def advance_clock(clock: Dict[str, int], hours: int = 1) -> Dict[str, int]:
    """Pure clock arithmetic. Returns a NEW clock dict; the input is
    untouched. hours must be a non-negative int -- the engine never
    ticks backwards or by fractions; anything else raises ValueError.
    """
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 0:
        raise ValueError(f"hours must be a non-negative int, got {hours!r}")
    try:
        day = int(clock["day"])
        hour = int(clock["hour"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"clock must carry int day/hour, got {clock!r}")
    if not 0 <= hour < HOURS_PER_DAY:
        raise ValueError(f"clock hour out of range: {hour}")
    total = hour + hours
    return {"day": day + total // HOURS_PER_DAY, "hour": total % HOURS_PER_DAY}


def format_clock(clock: Dict[str, int]) -> str:
    """Day 1, 08:00 -- the form the prompt context and the player see."""
    return f"Day {int(clock['day'])}, {int(clock['hour']):02d}:00"


def time_of_day(hour: int) -> str:
    """morning 05-11, afternoon 12-17, evening 18-21, night 22-04."""
    for start, end, label in _TIME_OF_DAY:
        if start <= hour < end:
            return label
    return "night"


def advance_tick(
    clock: Dict[str, int], hours: int = 1
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    """One tick: advance the clock and return (new_clock, log).

    The log is a list of entries, one per effect -- slice 8 logs only
    the clock itself. Later slices append agenda, rumor, standing,
    and event entries here, in this same list, so the turn metadata
    always shows everything the tick did.
    """
    new = advance_clock(clock, hours)
    log = [{
        "kind": "clock",
        "day": new["day"],
        "hour": new["hour"],
        "time_of_day": time_of_day(new["hour"]),
    }]
    return new, log


def location_at_hour(npc_info: Optional[Dict[str, Any]], hour: int) -> Optional[str]:
    """Where the NPC's schedule puts them at this hour, or None.

    Schedule entries are {"start", "end", "location"}; start is
    inclusive, end exclusive; start > end wraps midnight (22 -> 6
    covers 22, 23, 0..5). First matching entry wins. Malformed
    entries are skipped, never fatal -- world files are author data.
    No schedule, or no entry matching this hour, means None: the NPC
    defaults home.
    """
    schedule = (npc_info or {}).get("schedule") or []
    for entry in schedule:
        try:
            start = int(entry["start"])
            end = int(entry["end"])
            loc = entry["location"]
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= start < HOURS_PER_DAY and 0 <= end < HOURS_PER_DAY):
            continue
        if not loc:
            continue
        if start <= end:
            hit = start <= hour < end
        else:  # Overnight wrap.
            hit = hour >= start or hour < end
        if hit:
            return loc
    return None


def is_present(
    npc_info: Optional[Dict[str, Any]],
    location_name: str,
    hour: int,
    home_location: str,
) -> bool:
    """Is this NPC findable in location_name at this hour?

    A schedule entry wins over home; silence in the schedule means
    home. An NPC with no schedule is always at home. The conversation
    partner is exempt from this check -- they are present by
    definition, because the player walked up to them.
    """
    scheduled = location_at_hour(npc_info, hour)
    effective = scheduled if scheduled is not None else home_location
    return effective == location_name


# ---- NPC agendas (§25, slice 9) -------------------------------------------
# A step is a dict: {"id", "duration_hours", "effect",
# "after_step"?, "after_time"?}. The effect is exactly one of
# {"fact": {"topic", "text"}} or {"move": {"location"}}.
# Steps run in order, one active step per NPC: the first not-done
# step, which accrues only while its preconditions hold. A blocked
# first step blocks the rest -- later steps wait their turn.
# At most one step fires per NPC per tick call: a multi-hour tick
# advances the active step by the full hours but does not cascade
# into the next step. The real tick points are 1 hour each, so
# this is exact in practice.
#
# Malformed steps are skipped with a warning, never fatal -- world
# files are author data. Structural validity (missing id/effect)
# is Pydantic's job at world load; everything semantic is checked
# here, per tick.
#
# This module only DESCRIBES effects: advance_agendas returns log
# entries, and the manager applies them (moves to npc_states,
# facts to world_facts). The simulation stays pure.

import logging as _logging

logger = _logging.getLogger(__name__)


def _validate_step(step: Any, valid_ids: set) -> Optional[Dict[str, Any]]:
    """One agenda step, normalized, or None if malformed.

    valid_ids is the set of ids belonging to structurally sound
    steps in this agenda -- an after_step naming anything else can
    never complete, so the step is skipped rather than stalling
    the agenda forever.
    """
    if not isinstance(step, dict):
        logger.warning("Agenda step is not an object, skipping: %r", step)
        return None
    sid = step.get("id")
    if not isinstance(sid, str) or not sid:
        logger.warning("Agenda step without a usable id, skipping: %r", step)
        return None
    duration = step.get("duration_hours")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        logger.warning(
            "Agenda step %r has bad duration_hours %r, skipping", sid, duration)
        return None
    after_step = step.get("after_step")
    if after_step is not None:
        if not isinstance(after_step, str) or after_step not in valid_ids:
            logger.warning(
                "Agenda step %r waits on unknown step %r, skipping",
                sid, after_step)
            return None
    after_time = step.get("after_time")
    if after_time is not None:
        if not isinstance(after_time, dict):
            logger.warning(
                "Agenda step %r has a bad after_time %r, skipping",
                sid, after_time)
            return None
        try:
            day = int(after_time["day"])
            hour = int(after_time["hour"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Agenda step %r has a bad after_time %r, skipping",
                sid, after_time)
            return None
        if isinstance(after_time.get("day"), bool) or isinstance(after_time.get("hour"), bool):
            logger.warning(
                "Agenda step %r has a bad after_time %r, skipping",
                sid, after_time)
            return None
        if day < 1 or not 0 <= hour < HOURS_PER_DAY:
            logger.warning(
                "Agenda step %r has a bad after_time %r, skipping",
                sid, after_time)
            return None
        after_time = {"day": day, "hour": hour}
    effect = step.get("effect")
    if not isinstance(effect, dict) or len(effect) != 1:
        logger.warning(
            "Agenda step %r has a bad effect %r, skipping", sid, effect)
        return None
    kind = next(iter(effect))
    if kind == "fact":
        body = effect["fact"]
        if not isinstance(body, dict):
            logger.warning(
                "Agenda step %r has a bad fact effect %r, skipping", sid, effect)
            return None
        topic = body.get("topic")
        text = body.get("text")
        if not isinstance(topic, str) or not topic or not isinstance(text, str) or not text:
            logger.warning(
                "Agenda step %r has a bad fact effect %r, skipping", sid, effect)
            return None
        effect = {"kind": "fact", "topic": topic, "text": text}
    elif kind == "move":
        body = effect["move"]
        if not isinstance(body, dict):
            logger.warning(
                "Agenda step %r has a bad move effect %r, skipping", sid, effect)
            return None
        location = body.get("location")
        if not isinstance(location, str) or not location:
            logger.warning(
                "Agenda step %r has a bad move effect %r, skipping", sid, effect)
            return None
        effect = {"kind": "move", "location": location}
    else:
        logger.warning(
            "Agenda step %r has an unknown effect kind %r, skipping", sid, kind)
        return None
    return {
        "id": sid,
        "duration_hours": duration,
        "after_step": after_step,
        "after_time": after_time,
        "effect": effect,
    }


def _preconditions_hold(step: Dict[str, Any], done: set,
                        clock: Dict[str, int]) -> bool:
    """after_step must be done; after_time must have arrived. Both
    combine with AND. Preconditions are checked against the NEW
    clock -- at noon, the noon work begins."""
    if step["after_step"] is not None and step["after_step"] not in done:
        return False
    after_time = step["after_time"]
    if after_time is not None:
        if (clock["day"], clock["hour"]) < (after_time["day"], after_time["hour"]):
            return False
    return True


def advance_agendas(
    agendas: Optional[Dict[str, Dict[str, Any]]],
    hours: int,
    clock: Dict[str, int],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Advance every NPC's agenda by `hours` against `clock`.

    agendas is {npc_name: {"agenda": [step dicts...],
    "progress": {"current": id-or-None, "elapsed": N, "done":
    [ids]}}}. Returns (new_progress, log) -- new_progress is the
    full per-NPC progress dict (NPCs without an agenda get no
    entry), and log holds one entry per fired effect, in NPC name
    order so the log is deterministic. Inputs are never mutated.
    hours must be a non-negative int; clock must carry int
    day/hour with hour in range -- anything else raises
    ValueError, same as advance_clock.
    """
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 0:
        raise ValueError(f"hours must be a non-negative int, got {hours!r}")
    try:
        day = int(clock["day"])
        hour = int(clock["hour"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"clock must carry int day/hour, got {clock!r}")
    if isinstance(clock.get("day"), bool) or isinstance(clock.get("hour"), bool):
        raise ValueError(f"clock must carry int day/hour, got {clock!r}")
    if not 0 <= hour < HOURS_PER_DAY:
        raise ValueError(f"clock hour out of range: {hour}")
    clock = {"day": day, "hour": hour}

    new_progress: Dict[str, Dict[str, Any]] = {}
    for npc, spec in (agendas or {}).items():
        if not isinstance(spec, dict):
            continue
        old = spec.get("progress") or {}
        if not isinstance(old, dict):
            old = {}
        done = [d for d in (old.get("done") or []) if isinstance(d, str)]
        new_progress[npc] = {
            "current": old.get("current") if isinstance(old.get("current"), str) else None,
            "elapsed": old.get("elapsed") if isinstance(old.get("elapsed"), int) and not isinstance(old.get("elapsed"), bool) and old.get("elapsed") >= 0 else 0,
            "done": list(done),
        }

    log: List[Dict[str, Any]] = []
    for npc in sorted(agendas or {}):
        spec = agendas[npc]
        if not isinstance(spec, dict):
            continue
        raw_steps = spec.get("agenda") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            # No agenda, or not a list: nothing to advance, and no
            # progress entry -- an NPC without an agenda leaves no
            # trace in the tick state.
            new_progress.pop(npc, None)
            continue
        # Pass 1: ids of steps with a usable id, so after_step can
        # be checked against the agenda as written.
        ids = {s.get("id") for s in raw_steps
               if isinstance(s, dict) and isinstance(s.get("id"), str) and s.get("id")}
        valid = [s for s in (_validate_step(s, ids) for s in raw_steps)
                 if s is not None]
        # Pass 2: an after_step naming a step that did not survive
        # validation can never complete -- skip it too, rather
        # than letting it wait forever.
        valid_ids = {s["id"] for s in valid}
        valid = [s for s in valid
                 if s["after_step"] is None or s["after_step"] in valid_ids]
        progress = new_progress[npc]
        done_set = set(progress["done"])
        pending = [s for s in valid if s["id"] not in done_set]
        if not pending:
            progress["current"] = None
            progress["elapsed"] = 0
            continue
        first = pending[0]
        if not _preconditions_hold(first, done_set, clock):
            # Waiting on time or a previous step: no accrual, and
            # the rest of the agenda waits behind it.
            progress["current"] = None
            progress["elapsed"] = 0
            continue
        if progress["current"] != first["id"]:
            # A new step (or an agenda edited between sessions):
            # elapsed belongs to the step, so it restarts.
            progress["elapsed"] = 0
        progress["elapsed"] += hours
        if progress["elapsed"] >= first["duration_hours"]:
            progress["done"].append(first["id"])
            progress["current"] = None
            progress["elapsed"] = 0
            log.append({
                "kind": "agenda",
                "npc": npc,
                "step": first["id"],
                "effect": first["effect"],
                "day": clock["day"],
                "hour": clock["hour"],
            })
        else:
            progress["current"] = first["id"]
    return new_progress, log

# ---- Rumor spread (§25, slice 10) -----------------------------------------
# Slice 7 records rumors with verdicts; the tick moves them along
# the relationship graph. A tells B if they are connected and the
# rumor is fresh; confidence decays per hop. The engine knows
# exactly who heard what -- the part the model could never be
# trusted to track.
#
# The record is the ledger: each record carries `heard_by`
# ([{npc, hop}], starting as [{said_by, 0}]), a `confidence`
# ("medium" at recording -- a rumor is secondhand by definition),
# and a `recorded_at` clock stamp for freshness. The spread writes
# one knowledge entry per listener into the session (not the world
# file), flagged rumor with provenance, so the listener can share
# it with the player through the existing knowledge_shared path.
#
# One hop per knower per tick call: a listener added this tick
# does not spread further until the next tick, so the rumor walks
# the graph over days instead of cascading in one call -- the same
# discipline as agendas. Freshness bounds the spread (24 hours);
# confidence bounds the effect (low applies nothing, §24), not
# the travel: a low-confidence rumor still moves, it just moves
# no dice. Only open records spread -- a corrected or confirmed
# record is settled, and the rumor as a rumor is dead.
#
# This module only DESCRIBES effects: advance_rumors returns the
# updated records, the new entries by listener, and the log, and
# the manager applies them. The simulation stays pure.

import hashlib as _hashlib
import re as _re

RUMOR_FRESH_HOURS = 24
_CONFIDENCE_STEPS = ("high", "medium", "low")


def _decay_confidence(confidence: Any, hops: int) -> str:
    """One step down per hop, flooring at low. An unknown
    confidence starts at medium -- secondhand, like every rumor
    (§24 treats unknown as medium for scaling)."""
    if confidence not in _CONFIDENCE_STEPS:
        confidence = "medium"
    idx = _CONFIDENCE_STEPS.index(confidence) + max(0, int(hops))
    return _CONFIDENCE_STEPS[min(idx, len(_CONFIDENCE_STEPS) - 1)]


def _slug(text: Any) -> str:
    """A short deterministic slug for entry ids: lowercase, runs
    of non-alphanumerics become one dash, capped at 40 chars."""
    slug = _re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    return slug.strip("-")[:40] or "rumor"


def _total_hours(clock: Dict[str, int]) -> int:
    return (int(clock["day"]) - 1) * HOURS_PER_DAY + int(clock["hour"])


def _recorded_total(record: Dict[str, Any]) -> Optional[int]:
    """Total hours the record was stamped at, or None when the
    record carries no usable stamp -- records written before
    this slice have none, and a malformed stamp is treated the
    same: the engine cannot prove them stale, so they are fresh."""
    stamped = record.get("recorded_at")
    if not isinstance(stamped, dict):
        return None
    try:
        day = int(stamped["day"])
        hour = int(stamped["hour"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Rumor record %r has a bad recorded_at %r; "
                       "treating it as fresh", record.get("subject"), stamped)
        return None
    if isinstance(stamped.get("day"), bool) or isinstance(stamped.get("hour"), bool):
        logger.warning("Rumor record %r has a bad recorded_at %r; "
                       "treating it as fresh", record.get("subject"), stamped)
        return None
    if day < 1 or not 0 <= hour < HOURS_PER_DAY:
        logger.warning("Rumor record %r has a bad recorded_at %r; "
                       "treating it as fresh", record.get("subject"), stamped)
        return None
    return (day - 1) * HOURS_PER_DAY + hour


def build_rumor_graph(
    connections: Optional[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """The undirected relationship graph from world-file data:
    {npc: [names it lists in `connections`]}. A-B are connected
    when either one lists the other, so an author writes each
    link once. A name the world does not know (no entry of its
    own) is dropped with a warning; self-links are dropped.
    Returns {npc: sorted [connected names]}; NPCs with no links
    get no entry. Inputs are never mutated."""
    known = {n for n in (connections or {}) if isinstance(n, str) and n}
    graph: Dict[str, set] = {}
    for npc, links in (connections or {}).items():
        if not isinstance(npc, str) or not npc:
            continue
        if not isinstance(links, list):
            logger.warning("Connections for %r are not a list, skipping", npc)
            continue
        for other in links:
            if not isinstance(other, str) or not other:
                continue
            if other == npc:
                continue
            if other not in known:
                logger.warning(
                    "Connections for %r name unknown NPC %r, skipping",
                    npc, other)
                continue
            graph.setdefault(npc, set()).add(other)
            graph.setdefault(other, set()).add(npc)
    return {npc: sorted(peers) for npc, peers in sorted(graph.items())}


def advance_rumors(
    records: Optional[List[Dict[str, Any]]],
    graph: Optional[Dict[str, List[str]]],
    clock: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]],
           List[Dict[str, Any]]]:
    """Spread every open, fresh rumor one hop along the graph.

    records is the session's rumor records (slice 7, plus the
    slice-10 fields -- missing ones are backfilled here, so
    records written before this slice spread rather than
    breaking). graph is build_rumor_graph's output. clock is
    the NEW clock -- freshness is measured against it.

    Returns (new_records, new_entries, log). new_records is the
    full list with heard_by/confidence/recorded_at backfilled
    and this tick's listeners appended; new_entries maps each
    listener to the knowledge entries it gained this tick; log
    holds one entry per hop, in record order and, within a
    record, knower then listener name order, so the log is
    deterministic. Inputs are never mutated.
    """
    try:
        day = int(clock["day"])
        hour = int(clock["hour"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"clock must carry int day/hour, got {clock!r}")
    if isinstance(clock.get("day"), bool) or isinstance(clock.get("hour"), bool):
        raise ValueError(f"clock must carry int day/hour, got {clock!r}")
    if not 0 <= hour < HOURS_PER_DAY:
        raise ValueError(f"clock hour out of range: {hour}")
    clock = {"day": day, "hour": hour}
    now_total = _total_hours(clock)

    graph = graph or {}
    new_records: List[Dict[str, Any]] = []
    new_entries: Dict[str, List[Dict[str, Any]]] = {}
    log: List[Dict[str, Any]] = []

    for record in records or []:
        if not isinstance(record, dict):
            logger.warning("Rumor record is not an object, skipping: %r", record)
            continue
        updated = dict(record)
        subject = updated.get("subject")
        text = updated.get("text")
        said_by = updated.get("said_by")
        # Backfill the slice-10 fields on records that lack them.
        heard_by = updated.get("heard_by")
        if not isinstance(heard_by, list):
            heard_by = []
        clean_heard = []
        for entry in heard_by:
            if (isinstance(entry, dict) and isinstance(entry.get("npc"), str)
                    and entry.get("npc")
                    and isinstance(entry.get("hop"), int)
                    and not isinstance(entry.get("hop"), bool)
                    and entry["hop"] >= 0):
                clean_heard.append({"npc": entry["npc"], "hop": entry["hop"]})
        if not clean_heard and isinstance(said_by, str) and said_by:
            clean_heard = [{"npc": said_by, "hop": 0}]
        updated["heard_by"] = clean_heard
        if updated.get("confidence") not in _CONFIDENCE_STEPS:
            updated["confidence"] = "medium"
        new_records.append(updated)

        if updated.get("status") != "open":
            # Settled records do not spread: the truth is out and
            # the rumor as a rumor is dead.
            continue
        if not isinstance(subject, str) or not subject:
            logger.warning("Rumor record without a subject cannot spread: %r", record)
            continue
        if not isinstance(text, str) or not text:
            logger.warning("Rumor record without text cannot spread: %r", record)
            continue
        stamped_total = _recorded_total(updated)
        if stamped_total is not None and now_total - stamped_total > RUMOR_FRESH_HOURS:
            # Stale: the rumor has had its day. No warning -- going
            # stale is the normal end of a rumor, not bad data.
            continue
        heard_names = {h["npc"] for h in clean_heard}
        # Snapshot the knowers: a listener added this tick does not
        # spread further until the next tick.
        knowers = sorted(clean_heard, key=lambda h: h["npc"])
        for knower in knowers:
            for listener in graph.get(knower["npc"], []):
                if listener in heard_names:
                    continue
                hop = knower["hop"] + 1
                confidence = _decay_confidence(updated["confidence"], hop)
                entry_id = (
                    f"rumor-{_slug(subject)}-"
                    f"{_hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
                )
                entry = {
                    "id": entry_id,
                    "topic": subject,
                    "fact": text,
                    "source": "heard_from",
                    "confidence": confidence,
                    "dc_modifier": 0,
                    "provenance": {
                        "source": "rumor-spread",
                        "from_npc": knower["npc"],
                        "hop": hop,
                        "subject": subject,
                        "day": clock["day"],
                        "hour": clock["hour"],
                    },
                }
                new_entries.setdefault(listener, []).append(entry)
                updated["heard_by"] = updated["heard_by"] + [
                    {"npc": listener, "hop": hop}]
                heard_names.add(listener)
                log.append({
                    "kind": "rumor",
                    "subject": subject,
                    "text": text,
                    "from": knower["npc"],
                    "to": listener,
                    "hop": hop,
                    "confidence": confidence,
                    "day": clock["day"],
                    "hour": clock["hour"],
                })
    return new_records, new_entries, log
