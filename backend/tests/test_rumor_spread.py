"""Slice 10 unit tests: rumor spread (the tick moves rumors along
the relationship graph, §25/§27).

Pure functions only -- no model, no database. The graph is a
four-NPC chain: Joren - Brom - Elara - Corvin. Joren tells a rumor
about Corvin; it walks one hop per tick, confidence decaying per
hop, and stops at a stale record, a settled record, or the edge
of the graph.
"""
import copy
import sys
import os
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.simulation import (
    RUMOR_FRESH_HOURS, advance_rumors, build_rumor_graph,
)

TEXT = "Corvin camps in the Whispering Woods."
GRAPH = build_rumor_graph({
    "Joren": ["Brom"],
    "Brom": ["Elara"],
    "Elara": ["Corvin"],
    "Corvin": [],
})


def _record(**overrides):
    record = {
        "subject": "Corvin",
        "text": TEXT,
        "said_by": "Joren",
        "turn": 2,
        "verdict": "unknown",
        "status": "open",
        "heard_by": [{"npc": "Joren", "hop": 0}],
        "confidence": "medium",
        "recorded_at": {"day": 1, "hour": 8},
    }
    record.update(overrides)
    return record


class TestBuildRumorGraph(unittest.TestCase):
    def test_undirected_each_link_written_once(self):
        graph = build_rumor_graph({"Joren": ["Brom"], "Brom": []})
        self.assertEqual(graph, {"Brom": ["Joren"], "Joren": ["Brom"]})

    def test_unknown_names_and_self_links_dropped(self):
        graph = build_rumor_graph({
            "Joren": ["Brom", "Nobody", "Joren"],
            "Brom": [],
        })
        self.assertEqual(graph, {"Brom": ["Joren"], "Joren": ["Brom"]})

    def test_non_list_connections_skipped(self):
        graph = build_rumor_graph({"Joren": "Brom", "Brom": []})
        self.assertEqual(graph, {})

    def test_empty_graph(self):
        self.assertEqual(build_rumor_graph({}), {})
        self.assertEqual(build_rumor_graph(None), {})


class TestAdvanceRumors(unittest.TestCase):
    def test_connected_pair_spreads_one_hop(self):
        records, entries, log = advance_rumors(
            [_record()], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(list(entries), ["Brom"])
        entry = entries["Brom"][0]
        self.assertEqual(entry["topic"], "Corvin")
        self.assertEqual(entry["fact"], TEXT)
        self.assertEqual(entry["source"], "heard_from")
        # Medium at the teller, one hop down: low.
        self.assertEqual(entry["confidence"], "low")
        self.assertEqual(entry["dc_modifier"], 0)
        self.assertEqual(entry["provenance"]["from_npc"], "Joren")
        self.assertEqual(entry["provenance"]["hop"], 1)
        self.assertEqual(records[0]["heard_by"],
                         [{"npc": "Joren", "hop": 0},
                          {"npc": "Brom", "hop": 1}])
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["kind"], "rumor")
        self.assertEqual((log[0]["from"], log[0]["to"]), ("Joren", "Brom"))
        self.assertEqual(log[0]["hop"], 1)
        self.assertEqual(log[0]["confidence"], "low")

    def test_one_hop_per_tick_no_cascade(self):
        # A single call walks one edge only: Elara and Corvin do
        # not hear it until later ticks, even though the chain is
        # there.
        _, entries, log = advance_rumors(
            [_record()], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(list(entries), ["Brom"])
        self.assertEqual(len(log), 1)

    def test_walks_the_chain_over_ticks(self):
        records = [_record()]
        heard = []
        for hour in (9, 10, 11):
            records, entries, log = advance_rumors(
                records, GRAPH, {"day": 1, "hour": hour})
            heard.append(sorted(entries))
        self.assertEqual(heard, [["Brom"], ["Elara"], ["Corvin"]])
        self.assertEqual(
            records[0]["heard_by"],
            [{"npc": "Joren", "hop": 0}, {"npc": "Brom", "hop": 1},
             {"npc": "Elara", "hop": 2}, {"npc": "Corvin", "hop": 3}])

    def test_unconnected_pair_does_not_spread(self):
        _, entries, log = advance_rumors(
            [_record()], build_rumor_graph({"Joren": [], "Brom": []}),
            {"day": 1, "hour": 9})
        self.assertEqual(entries, {})
        self.assertEqual(log, [])

    def test_decay_high_medium_low_then_floor(self):
        records = [_record(confidence="high")]
        confidences = []
        for hour in (9, 10, 11):
            records, entries, _ = advance_rumors(
                records, GRAPH, {"day": 1, "hour": hour})
            confidences.append(
                next(iter(entries.values()))[0]["confidence"])
        # High at the teller: Brom medium, Elara low, Corvin low
        # (floored -- confidence never drops below low).
        self.assertEqual(confidences, ["medium", "low", "low"])

    def test_stale_record_does_not_spread(self):
        # Recorded Day 1, 08:00; 25 hours later it has had its day.
        records, entries, log = advance_rumors(
            [_record()], GRAPH, {"day": 2, "hour": 9})
        self.assertEqual(entries, {})
        self.assertEqual(log, [])
        self.assertEqual(records[0]["heard_by"],
                         [{"npc": "Joren", "hop": 0}])

    def test_exactly_fresh_at_24_hours(self):
        # Age exactly RUMOR_FRESH_HOURS still spreads.
        _, entries, _ = advance_rumors(
            [_record()], GRAPH, {"day": 2, "hour": 8})
        self.assertEqual(list(entries), ["Brom"])

    def test_record_without_stamp_is_fresh(self):
        record = _record()
        del record["recorded_at"]
        _, entries, _ = advance_rumors(
            [record], GRAPH, {"day": 9, "hour": 9})
        self.assertEqual(list(entries), ["Brom"])

    def test_settled_records_do_not_spread(self):
        for status in ("corrected", "confirmed"):
            _, entries, log = advance_rumors(
                [_record(status=status)], GRAPH, {"day": 1, "hour": 9})
            self.assertEqual(entries, {})
            self.assertEqual(log, [])

    def test_unknown_verdict_spreads(self):
        # Unknown is still gossip: the engine could not grade it,
        # and the world talks about it anyway.
        _, entries, _ = advance_rumors(
            [_record(verdict="unknown")], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(list(entries), ["Brom"])

    def test_false_rumor_spreads_until_corrected(self):
        _, entries, _ = advance_rumors(
            [_record(verdict="false")], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(list(entries), ["Brom"])

    def test_listener_hears_it_once(self):
        records = [_record()]
        records, first, _ = advance_rumors(
            records, GRAPH, {"day": 1, "hour": 9})
        records, second, _ = advance_rumors(
            records, GRAPH, {"day": 1, "hour": 10})
        # Second tick: Brom already heard it, so only Elara is new.
        self.assertEqual(list(first), ["Brom"])
        self.assertEqual(list(second), ["Elara"])
        brom_entries = [e for e in second.get("Brom", [])]
        self.assertEqual(brom_entries, [])

    def test_teller_is_never_re_told(self):
        # The graph links Brom back to Joren; Joren already heard
        # it (he said it), so no entry comes back to him.
        records = [_record()]
        for hour in (9, 10):
            records, entries, _ = advance_rumors(
                records, GRAPH, {"day": 1, "hour": hour})
        self.assertNotIn("Joren", entries)

    def test_backfills_records_missing_spread_fields(self):
        record = {"subject": "Corvin", "text": TEXT, "said_by": "Joren",
                  "turn": 2, "verdict": "unknown", "status": "open"}
        records, entries, _ = advance_rumors(
            [record], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(records[0]["heard_by"],
                         [{"npc": "Joren", "hop": 0},
                          {"npc": "Brom", "hop": 1}])
        self.assertEqual(records[0]["confidence"], "medium")
        self.assertEqual(list(entries), ["Brom"])

    def test_malformed_records_skipped_never_fatal(self):
        records, entries, log = advance_rumors(
            [_record(), {"subject": "Corvin"}, "not a record",
             _record(subject="")],
            GRAPH, {"day": 1, "hour": 9})
        # Only the well-formed record spreads; the rest are skipped.
        self.assertEqual(len(records), 3)
        self.assertEqual(list(entries), ["Brom"])
        self.assertEqual(len(log), 1)

    def test_entry_id_is_stable_per_rumor(self):
        _, first, _ = advance_rumors(
            [_record()], GRAPH, {"day": 1, "hour": 9})
        _, second, _ = advance_rumors(
            [_record()], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(first["Brom"][0]["id"], second["Brom"][0]["id"])
        # And per rumor, not per listener: Elara's entry for the
        # same rumor carries the same id, so the player learns it
        # once no matter who tells them.
        records = [_record()]
        records, _, _ = advance_rumors(records, GRAPH, {"day": 1, "hour": 9})
        _, entries, _ = advance_rumors(
            records, GRAPH, {"day": 1, "hour": 10})
        self.assertEqual(entries["Elara"][0]["id"], first["Brom"][0]["id"])

    def test_deterministic(self):
        first = advance_rumors([_record()], GRAPH, {"day": 1, "hour": 9})
        second = advance_rumors([_record()], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(first, second)

    def test_inputs_never_mutated(self):
        record = _record()
        before = copy.deepcopy(record)
        advance_rumors([record], GRAPH, {"day": 1, "hour": 9})
        self.assertEqual(record, before)

    def test_bad_clock_raises(self):
        with self.assertRaises(ValueError):
            advance_rumors([_record()], GRAPH, {"day": 1, "hour": 25})
        with self.assertRaises(ValueError):
            advance_rumors([_record()], GRAPH, {"day": 1, "hour": True})


if __name__ == "__main__":
    unittest.main()
