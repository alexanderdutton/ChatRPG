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
