"""Slice 8 unit tests: the clock, schedules, and the tick itself.

Pure functions on state -- no database, no world, no model. The
point of the tick engine (section 25) is that it is deterministic,
so these tests pin exact values: the clock after N hours, who is
where at hour H, and the tick log.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.simulation import (
    advance_clock,
    advance_tick,
    format_clock,
    is_present,
    location_at_hour,
    new_clock,
    time_of_day,
)


class TestClockArithmetic(unittest.TestCase):
    def test_new_clock_starts_day_one_morning(self):
        self.assertEqual(new_clock(), {"day": 1, "hour": 8})

    def test_one_hour_within_the_day(self):
        self.assertEqual(advance_clock({"day": 1, "hour": 8}, 1),
                         {"day": 1, "hour": 9})

    def test_crosses_midnight_into_the_next_day(self):
        self.assertEqual(advance_clock({"day": 1, "hour": 8}, 17),
                         {"day": 2, "hour": 1})

    def test_full_day_keeps_the_hour(self):
        self.assertEqual(advance_clock({"day": 1, "hour": 8}, 24),
                         {"day": 2, "hour": 8})

    def test_zero_hours_returns_a_copy(self):
        clock = {"day": 3, "hour": 14}
        result = advance_clock(clock, 0)
        self.assertEqual(result, clock)
        self.assertIsNot(result, clock)

    def test_input_is_never_mutated(self):
        clock = {"day": 1, "hour": 22}
        advance_clock(clock, 5)
        self.assertEqual(clock, {"day": 1, "hour": 22})

    def test_negative_hours_rejected(self):
        with self.assertRaises(ValueError):
            advance_clock({"day": 1, "hour": 8}, -1)

    def test_fractional_hours_rejected(self):
        with self.assertRaises(ValueError):
            advance_clock({"day": 1, "hour": 8}, 1.5)

    def test_bool_hours_rejected(self):
        # bool is an int subclass; True must not sneak through as 1.
        with self.assertRaises(ValueError):
            advance_clock({"day": 1, "hour": 8}, True)

    def test_bad_clock_rejected(self):
        with self.assertRaises(ValueError):
            advance_clock({"day": 1}, 1)
        with self.assertRaises(ValueError):
            advance_clock({"day": 1, "hour": 25}, 1)

    def test_format_clock(self):
        self.assertEqual(format_clock({"day": 1, "hour": 8}),
                         "Day 1, 08:00")
        self.assertEqual(format_clock({"day": 2, "hour": 1}),
                         "Day 2, 01:00")

    def test_time_of_day_boundaries(self):
        cases = {0: "night", 4: "night", 5: "morning", 11: "morning",
                 12: "afternoon", 17: "afternoon", 18: "evening",
                 21: "evening", 22: "night", 23: "night"}
        for hour, expected in cases.items():
            with self.subTest(hour=hour):
                self.assertEqual(time_of_day(hour), expected)


class TestTick(unittest.TestCase):
    def test_tick_advances_and_logs_the_clock(self):
        new, log = advance_tick({"day": 1, "hour": 8}, 1)
        self.assertEqual(new, {"day": 1, "hour": 9})
        self.assertEqual(log, [{"kind": "clock", "day": 1, "hour": 9,
                                "time_of_day": "morning"}])

    def test_tick_defaults_to_one_hour(self):
        new, log = advance_tick({"day": 1, "hour": 23})
        self.assertEqual(new, {"day": 2, "hour": 0})
        self.assertEqual(log[0]["time_of_day"], "night")

    def test_tick_is_deterministic(self):
        # Same seed plus same inputs gives the same world state --
        # the property the whole tick engine is built on.
        first = advance_tick({"day": 1, "hour": 8}, 5)
        second = advance_tick({"day": 1, "hour": 8}, 5)
        self.assertEqual(first, second)

    def test_tick_does_not_mutate_its_input(self):
        clock = {"day": 1, "hour": 8}
        advance_tick(clock, 3)
        self.assertEqual(clock, {"day": 1, "hour": 8})


class TestSchedules(unittest.TestCase):
    def setUp(self):
        self.elara = {"name": "Elara", "schedule": [
            {"start": 8, "end": 12, "location": "Oakhaven"},
            {"start": 12, "end": 20, "location": "Moonpetal Glade"},
        ]}

    def test_no_schedule_means_no_scheduled_location(self):
        self.assertIsNone(location_at_hour({"name": "Brom"}, 10))
        self.assertIsNone(location_at_hour(None, 10))
        self.assertIsNone(location_at_hour({"name": "Brom", "schedule": []}, 10))

    def test_simple_range_hit_and_miss(self):
        self.assertEqual(location_at_hour(self.elara, 8), "Oakhaven")
        self.assertEqual(location_at_hour(self.elara, 11), "Oakhaven")
        self.assertEqual(location_at_hour(self.elara, 15), "Moonpetal Glade")

    def test_end_is_exclusive(self):
        # 12:00 is the glade's hour, not Oakhaven's last one.
        self.assertEqual(location_at_hour(self.elara, 12), "Moonpetal Glade")

    def test_overnight_wrap(self):
        night_owl = {"name": "Wren", "schedule": [
            {"start": 22, "end": 6, "location": "Watchtower"},
        ]}
        self.assertEqual(location_at_hour(night_owl, 22), "Watchtower")
        self.assertEqual(location_at_hour(night_owl, 0), "Watchtower")
        self.assertEqual(location_at_hour(night_owl, 5), "Watchtower")
        self.assertIsNone(location_at_hour(night_owl, 6))
        self.assertIsNone(location_at_hour(night_owl, 21))

    def test_first_match_wins(self):
        overlapping = {"name": "Pip", "schedule": [
            {"start": 8, "end": 18, "location": "Market"},
            {"start": 10, "end": 12, "location": "Smithy"},
        ]}
        self.assertEqual(location_at_hour(overlapping, 11), "Market")

    def test_malformed_entries_are_skipped_not_fatal(self):
        sloppy = {"name": "Pip", "schedule": [
            {"start": 8, "end": 12},  # no location
            {"start": "ten", "end": 12, "location": "Smithy"},  # bad hour
            {"start": 25, "end": 26, "location": "Nowhere"},  # out of range
            {"start": 12, "end": 18, "location": "Market"},
        ]}
        self.assertEqual(location_at_hour(sloppy, 13), "Market")
        self.assertIsNone(location_at_hour(sloppy, 10))

    def test_is_present_no_schedule_means_home(self):
        brom = {"name": "Brom"}
        self.assertTrue(is_present(brom, "Oakhaven", 3, "Oakhaven"))
        self.assertFalse(is_present(brom, "Moonpetal Glade", 3, "Oakhaven"))

    def test_is_present_follows_the_schedule(self):
        self.assertTrue(is_present(self.elara, "Oakhaven", 9, "Oakhaven"))
        self.assertFalse(is_present(self.elara, "Oakhaven", 15, "Oakhaven"))
        self.assertTrue(is_present(self.elara, "Moonpetal Glade", 15, "Oakhaven"))

    def test_is_present_gap_means_home(self):
        # Silence in the schedule means home, not offstage: an author
        # writes where the NPC goes, and the rest of the day they are
        # where they live.
        self.assertTrue(is_present(self.elara, "Oakhaven", 21, "Oakhaven"))
        self.assertFalse(is_present(self.elara, "Moonpetal Glade", 21, "Oakhaven"))


if __name__ == "__main__":
    unittest.main()
