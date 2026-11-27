"""Slice 9 unit tests: NPC agendas (section 25).

Pure functions on state -- no database, no world, no model. An
agenda is a small state machine in the world file: steps with a
precondition, an effect, and a duration in ticks. The engine
advances it against the clock and describes the effects; it never
writes text and never invents entities.
"""
import copy
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.simulation import advance_agendas


def fact_step(step_id, duration, topic="the old codex", text="Done.",
              **extra):
    step = {"id": step_id, "duration_hours": duration,
            "effect": {"fact": {"topic": topic, "text": text}}}
    step.update(extra)
    return step


def move_step(step_id, duration, location="Moonpetal Glade", **extra):
    step = {"id": step_id, "duration_hours": duration,
            "effect": {"move": {"location": location}}}
    step.update(extra)
    return step


def agendas_for(npc, steps, progress=None):
    return {npc: {"agenda": steps, "progress": progress}}


class TestAgendaStepAdvance(unittest.TestCase):
    def test_duration_counts_ticks_and_fires_once(self):
        agendas = agendas_for("Elara", [fact_step("translate", 16)])
        clock = {"day": 1, "hour": 8}
        progress, log = advance_agendas(agendas, 15, clock)
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["current"], "translate")
        self.assertEqual(progress["Elara"]["elapsed"], 15)
        self.assertEqual(progress["Elara"]["done"], [])
        progress, log = advance_agendas(
            {"Elara": {"agenda": agendas["Elara"]["agenda"],
                       "progress": progress["Elara"]}}, 1, clock)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["kind"], "agenda")
        self.assertEqual(log[0]["npc"], "Elara")
        self.assertEqual(log[0]["step"], "translate")
        self.assertEqual(log[0]["effect"],
                         {"kind": "fact", "topic": "the old codex",
                          "text": "Done."})
        self.assertEqual(progress["Elara"]["done"], ["translate"])
        self.assertIsNone(progress["Elara"]["current"])
        self.assertEqual(progress["Elara"]["elapsed"], 0)

    def test_zero_duration_fires_on_the_first_tick(self):
        agendas = agendas_for("Elara", [fact_step("notice", 0)])
        _, log = advance_agendas(agendas, 1, {"day": 1, "hour": 8})
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["step"], "notice")

    def test_partial_hours_accumulate(self):
        agendas = agendas_for("Elara", [fact_step("translate", 10)])
        progress, log = advance_agendas(agendas, 4, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        progress, log = advance_agendas(
            {"Elara": {"agenda": agendas["Elara"]["agenda"],
                       "progress": progress["Elara"]}}, 6, {"day": 1, "hour": 12})
        self.assertEqual(len(log), 1)
        self.assertEqual(progress["Elara"]["done"], ["translate"])

    def test_multi_hour_tick_fires_at_most_one_step(self):
        # A 5-hour jump finishes the 2-hour step but does not cascade
        # into the next one: one firing per NPC per tick call.
        steps = [fact_step("first", 2, text="First done."),
                 fact_step("second", 2, text="Second done.")]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["step"], "first")
        self.assertEqual(progress["Elara"]["done"], ["first"])
        self.assertIsNone(progress["Elara"]["current"])
        progress, log = advance_agendas(
            {"Elara": {"agenda": steps, "progress": progress["Elara"]}},
            2, {"day": 1, "hour": 13})
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["step"], "second")

    def test_move_effect_lands_in_the_log(self):
        agendas = agendas_for("Brom", [move_step("pack-up", 8)])
        _, log = advance_agendas(agendas, 8, {"day": 1, "hour": 8})
        self.assertEqual(log, [{
            "kind": "agenda", "npc": "Brom", "step": "pack-up",
            "effect": {"kind": "move", "location": "Moonpetal Glade"},
            "day": 1, "hour": 8}])


class TestAgendaPreconditions(unittest.TestCase):
    def test_after_step_gates_the_next_step(self):
        steps = [fact_step("translate", 4, text="Translated."),
                 move_step("leave", 4, after_step="translate")]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 4, {"day": 1, "hour": 8})
        self.assertEqual([e["step"] for e in log], ["translate"])
        self.assertEqual(progress["Elara"]["done"], ["translate"])
        # The move accrues only now that its predecessor is done.
        progress, log = advance_agendas(
            {"Elara": {"agenda": steps, "progress": progress["Elara"]}},
            3, {"day": 1, "hour": 12})
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["current"], "leave")
        self.assertEqual(progress["Elara"]["elapsed"], 3)
        progress, log = advance_agendas(
            {"Elara": {"agenda": steps, "progress": progress["Elara"]}},
            1, {"day": 1, "hour": 15})
        self.assertEqual([e["step"] for e in log], ["leave"])

    def test_blocked_first_step_blocks_the_rest(self):
        # Step 1 waits on Day 3; step 2 has no precondition -- it
        # still does not jump the queue.
        steps = [fact_step("wait", 2, after_time={"day": 3, "hour": 8}),
                 fact_step("eager", 1)]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertIsNone(progress["Elara"]["current"])
        self.assertEqual(progress["Elara"]["done"], [])

    def test_after_time_uses_the_new_clock(self):
        # A tick from 11:00 to 12:00 activates the noon step in the
        # same tick: at noon, the noon work begins.
        steps = [fact_step("noon-work", 2, after_time={"day": 1, "hour": 12})]
        agendas = agendas_for("Elara", steps)
        _, log = advance_agendas(agendas, 1, {"day": 1, "hour": 12})
        self.assertEqual(log, [])
        progress, _ = advance_agendas(agendas, 1, {"day": 1, "hour": 12})
        self.assertEqual(progress["Elara"]["current"], "noon-work")
        self.assertEqual(progress["Elara"]["elapsed"], 1)
        _, log = advance_agendas(
            {"Elara": {"agenda": steps, "progress": progress["Elara"]}},
            1, {"day": 1, "hour": 13})
        self.assertEqual(len(log), 1)

    def test_after_time_before_it_arrives_does_not_accrue(self):
        steps = [fact_step("later", 2, after_time={"day": 2, "hour": 8})]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 3, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertIsNone(progress["Elara"]["current"])
        self.assertEqual(progress["Elara"]["elapsed"], 0)

    def test_both_preconditions_combine_with_and(self):
        steps = [fact_step("first", 1),
                 fact_step("second", 1, after_step="first",
                           after_time={"day": 2, "hour": 8})]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 1, {"day": 1, "hour": 8})
        self.assertEqual([e["step"] for e in log], ["first"])
        # Step 1 is done, but it is still Day 1: step 2 waits.
        progress, log = advance_agendas(
            {"Elara": {"agenda": steps, "progress": progress["Elara"]}},
            1, {"day": 1, "hour": 9})
        self.assertEqual(log, [])
        self.assertIsNone(progress["Elara"]["current"])


class TestAgendaDeterminismAndHygiene(unittest.TestCase):
    def test_same_sequence_twice_is_identical(self):
        steps = [fact_step("translate", 16),
                 move_step("leave", 8, after_step="translate")]
        first_agendas = agendas_for("Elara", steps)
        second_agendas = copy.deepcopy(first_agendas)
        clock = {"day": 1, "hour": 8}
        p1, l1 = advance_agendas(first_agendas, 16, clock)
        p2, l2 = advance_agendas(second_agendas, 16, clock)
        self.assertEqual(p1, p2)
        self.assertEqual(l1, l2)
        p1, l1 = advance_agendas(
            {"Elara": {"agenda": steps, "progress": p1["Elara"]}}, 8,
            {"day": 2, "hour": 0})
        p2, l2 = advance_agendas(
            {"Elara": {"agenda": steps, "progress": p2["Elara"]}}, 8,
            {"day": 2, "hour": 0})
        self.assertEqual(p1, p2)
        self.assertEqual(l1, l2)

    def test_inputs_are_never_mutated(self):
        steps = [fact_step("translate", 4),
                 move_step("leave", 4, after_step="translate")]
        agendas = agendas_for("Elara", steps, progress={
            "current": "translate", "elapsed": 2, "done": []})
        snapshot = copy.deepcopy(agendas)
        advance_agendas(agendas, 4, {"day": 1, "hour": 8})
        self.assertEqual(agendas, snapshot)

    def test_log_order_is_npc_name_order(self):
        agendas = {
            "Brom": {"agenda": [move_step("pack-up", 1)], "progress": None},
            "Elara": {"agenda": [fact_step("translate", 1)],
                      "progress": None},
        }
        _, log = advance_agendas(agendas, 1, {"day": 1, "hour": 8})
        self.assertEqual([e["npc"] for e in log], ["Brom", "Elara"])

    def test_npc_without_an_agenda_leaves_no_progress(self):
        agendas = {
            "Elara": {"agenda": [fact_step("translate", 1)],
                      "progress": None},
            "Brom": {"agenda": [], "progress": None},
            "Corvin": {"progress": None},
        }
        progress, log = advance_agendas(agendas, 1, {"day": 1, "hour": 8})
        self.assertIn("Elara", progress)
        self.assertNotIn("Brom", progress)
        self.assertNotIn("Corvin", progress)
        self.assertEqual([e["npc"] for e in log], ["Elara"])

    def test_elapsed_restarts_when_the_step_changes(self):
        # Progress claims to be 3 hours into "leave", but "leave"
        # is not the active step (its predecessor is not done) --
        # elapsed restarts when the real active step begins.
        steps = [fact_step("translate", 4),
                 move_step("leave", 4, after_step="translate")]
        agendas = agendas_for("Elara", steps, progress={
            "current": "leave", "elapsed": 3, "done": []})
        progress, log = advance_agendas(agendas, 2, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["current"], "translate")
        self.assertEqual(progress["Elara"]["elapsed"], 2)

    def test_bad_hours_and_clock_rejected(self):
        agendas = agendas_for("Elara", [fact_step("translate", 1)])
        with self.assertRaises(ValueError):
            advance_agendas(agendas, -1, {"day": 1, "hour": 8})
        with self.assertRaises(ValueError):
            advance_agendas(agendas, 1.5, {"day": 1, "hour": 8})
        with self.assertRaises(ValueError):
            advance_agendas(agendas, 1, {"day": 1, "hour": 24})
        with self.assertRaises(ValueError):
            advance_agendas(agendas, 1, {"day": 1})


class TestAgendaMalformedSteps(unittest.TestCase):
    def test_step_without_an_id_is_skipped(self):
        steps = [{"duration_hours": 1,
                  "effect": {"fact": {"topic": "x", "text": "y"}}}]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["done"], [])

    def test_bad_duration_is_skipped(self):
        for bad in (-1, 1.5, "four", True, None):
            steps = [fact_step("bad", bad)]
            agendas = agendas_for("Elara", steps)
            _, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
            self.assertEqual(log, [], f"duration {bad!r} should not fire")

    def test_bad_effect_is_skipped(self):
        bad_effects = [
            None, "fact", {"fact": {"topic": "x"}},  # missing text
            {"fact": {"text": "y"}},  # missing topic
            {"move": {}}, {"move": {"location": ""}},
            {"fact": {"topic": "x", "text": "y"},
             "move": {"location": "Z"}},  # two kinds at once
            {"dance": {"style": "waltz"}},  # unknown kind
        ]
        for effect in bad_effects:
            steps = [{"id": "bad", "duration_hours": 1, "effect": effect}]
            agendas = agendas_for("Elara", steps)
            _, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
            self.assertEqual(log, [], f"effect {effect!r} should not fire")

    def test_after_step_naming_no_step_is_skipped(self):
        steps = [fact_step("orphan", 1, after_step="ghost")]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["done"], [])

    def test_after_step_naming_a_malformed_step_is_skipped(self):
        # "broken" fails validation, so "waits" can never complete
        # -- it is skipped too, rather than waiting forever.
        steps = [fact_step("broken", -1),
                 fact_step("waits", 1, after_step="broken")]
        agendas = agendas_for("Elara", steps)
        progress, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        self.assertEqual(log, [])
        self.assertEqual(progress["Elara"]["done"], [])

    def test_bad_after_time_is_skipped(self):
        for bad_time in ({"day": 0, "hour": 8}, {"day": 1, "hour": 24},
                         {"day": 1}, {"hour": 8}, "noon", {"day": True, "hour": 8}):
            steps = [fact_step("bad", 1, after_time=bad_time)]
            agendas = agendas_for("Elara", steps)
            _, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
            self.assertEqual(log, [], f"after_time {bad_time!r} should not fire")

    def test_one_bad_step_does_not_block_a_good_one(self):
        steps = [fact_step("bad", -1),
                 fact_step("good", 1, text="Good done.")]
        agendas = agendas_for("Elara", steps)
        _, log = advance_agendas(agendas, 5, {"day": 1, "hour": 8})
        # The bad step is skipped; the good step is first in line.
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["step"], "good")


if __name__ == "__main__":
    unittest.main()
