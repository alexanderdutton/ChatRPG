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
