"""Slice 7 unit tests: gossip with reliability (§24).

Confidence scales the modifier (high full, medium halved, low
nothing); rumor claims are recorded with the engine's verdict; a
truth-grade fact (saw + high confidence) corrects or confirms an
open record. All pure functions of (ledger, subjects, check type)
or (validation, records) — no world, no session, no model.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.claims_validation import ClaimValidation, TurnValidation
from backend.knowledge import (
    add_rumor_records,
    knowledge_modifier_breakdown,
    knowledge_modifiers,
    reconcile_rumors,
    record_rumors,
)

HIGH = {"id": "k-high", "topic": "Corvin",
        "fact": "He takes the roots for his sick wife.",
        "source": "saw", "confidence": "high",
        "dc_modifier": -5, "applies_to": "charisma"}
MEDIUM = {"id": "k-medium", "topic": "Corvin",
          "fact": "He takes the roots for coin, they say.",
          "source": "heard_from", "confidence": "medium",
          "dc_modifier": -5, "applies_to": "charisma"}
LOW = {"id": "k-low", "topic": "Corvin",
       "fact": "Everyone says he is dangerous.",
       "source": "everyone_says", "confidence": "low",
       "dc_modifier": -5, "applies_to": "charisma"}
SMALL_MEDIUM = {"id": "k-small", "topic": "Corvin",
                "fact": "He is a little afraid of dogs.",
                "source": "heard_from", "confidence": "medium",
                "dc_modifier": -1, "applies_to": "charisma"}


def _check(ledger, **kwargs):
    kwargs.setdefault("subjects", ["Corvin"])
    kwargs.setdefault("check_type", "charisma")
    return knowledge_modifiers(ledger, **kwargs)


class TestConfidenceScaling(unittest.TestCase):
    def test_high_applies_in_full(self):
        self.assertEqual(_check([HIGH]), [-5])

    def test_medium_halves_toward_zero(self):
        self.assertEqual(_check([MEDIUM]), [-2])

    def test_medium_small_modifier_rounds_to_nothing(self):
        # Half of -1 rounds toward zero to 0, and zero modifiers are
        # excluded — a small modifier from a medium source vanishes,
        # honestly and visibly (the breakdown still shows it).
        self.assertEqual(_check([SMALL_MEDIUM]), [])

    def test_low_applies_nothing(self):
        self.assertEqual(_check([LOW]), [])

    def test_unknown_confidence_treated_as_medium(self):
        entry = dict(MEDIUM)
        del entry["confidence"]
        self.assertEqual(_check([entry]), [-2])

    def test_positive_modifier_halves_too(self):
        entry = dict(MEDIUM, dc_modifier=5)
        self.assertEqual(_check([entry]), [2])

    def test_mixed_ledger_scales_each_entry(self):
        self.assertEqual(_check([HIGH, MEDIUM, LOW]), [-5, -2])


class TestModifierBreakdown(unittest.TestCase):
    def test_breakdown_shows_listed_applied_and_reason(self):
        breakdown = knowledge_modifier_breakdown(
            [HIGH, MEDIUM, LOW], subjects=["Corvin"],
            check_type="charisma")
        self.assertEqual(
            [(b["id"], b["listed_modifier"], b["applied_modifier"])
             for b in breakdown],
            [("k-high", -5, -5), ("k-medium", -5, -2), ("k-low", -5, 0)])
        self.assertEqual(
            [b["reason"] for b in breakdown],
            ["high confidence — full modifier",
             "medium confidence — halved",
             "low confidence — no help"])

    def test_breakdown_sums_to_applied_modifiers(self):
        ledger = [HIGH, MEDIUM, LOW]
        self.assertEqual(
            sum(b["applied_modifier"] for b in
                knowledge_modifier_breakdown(
                    ledger, subjects=["Corvin"], check_type="charisma")),
            sum(_check(ledger)))

    def test_breakdown_empty_when_nothing_matches(self):
        self.assertEqual(
            knowledge_modifier_breakdown(
                [HIGH], subjects=["Brom"], check_type="charisma"),
            [])


def _validation(*claims):
    return TurnValidation(claims=[
        ClaimValidation(text=text, subject=subject, kind=kind,
                        verdict=verdict, action=action)
        for text, subject, kind, verdict, action in claims])


class TestRecordRumors(unittest.TestCase):
    def test_false_rumor_recorded_from_contradiction(self):
        records = record_rumors(_validation(
            ("Corvin camps in the Whispering Woods.", "Corvin",
             "rumor", "contradicts", "allow_wrong")), "Joren", 3)
        self.assertEqual(records, [{
            "subject": "Corvin",
            "text": "Corvin camps in the Whispering Woods.",
            "said_by": "Joren", "turn": 3,
            "verdict": "false", "status": "open"}])

    def test_true_rumor_recorded_from_grounded(self):
        records = record_rumors(_validation(
            ("Corvin lives in Oakhaven.", "Corvin",
             "rumor", "grounded", "accept")), "Joren", 3)
        self.assertEqual(records[0]["verdict"], "true")

    def test_uncheckable_rumor_recorded_unknown(self):
        for verdict in ("new_proposed", "ungrounded", "unverifiable"):
            records = record_rumors(_validation(
                ("Corvin takes the roots for coin.", "Corvin",
                 "rumor", verdict, "accept")), "Joren", 3)
            self.assertEqual(records[0]["verdict"], "unknown")

    def test_non_rumor_claims_ignored(self):
        records = record_rumors(_validation(
            ("Brom works at the smithy.", "Brom",
             "fact", "grounded", "accept")), "Joren", 3)
        self.assertEqual(records, [])

    def test_subjectless_rumor_ignored(self):
        records = record_rumors(_validation(
            ("Someone is taking the moonpetal.", None,
             "rumor", "unverifiable", "accept")), "Joren", 3)
        self.assertEqual(records, [])


class TestAddRumorRecords(unittest.TestCase):
    def _record(self, said_by="Joren", subject="Corvin",
                text="Corvin camps in the Whispering Woods."):
        return {"subject": subject, "text": text, "said_by": said_by,
                "turn": 3, "verdict": "false", "status": "open"}

    def test_repeat_from_same_npc_deduped(self):
        updated, newly = add_rumor_records(
            [self._record()], [self._record()])
        self.assertEqual(len(updated), 1)
        self.assertEqual(newly, [])

    def test_same_rumor_from_another_npc_is_a_new_record(self):
        updated, newly = add_rumor_records(
            [self._record()], [self._record(said_by="Brom")])
        self.assertEqual(len(updated), 2)
        self.assertEqual(len(newly), 1)

    def test_different_text_is_a_new_record(self):
        updated, newly = add_rumor_records(
            [self._record()],
            [self._record(text="Corvin is dangerous.")])
        self.assertEqual(len(updated), 2)


class TestReconcileRumors(unittest.TestCase):
    def _record(self, verdict="false", status="open"):
        return {"subject": "Corvin",
                "text": "Corvin camps in the Whispering Woods.",
                "said_by": "Joren", "turn": 3,
                "verdict": verdict, "status": status}

    def _truth(self, **overrides):
        entry = {"id": "corvin-k1", "topic": "Corvin",
                 "fact": "I take the roots for my sick wife.",
                 "source": "saw", "confidence": "high",
                 "dc_modifier": -5, "turn": 5}
        entry.update(overrides)
        return entry

    def test_saw_high_corrects_a_false_rumor(self):
        updated, flips = reconcile_rumors(
            [self._record("false")], [self._truth()])
        self.assertEqual(updated[0]["status"], "corrected")
        self.assertEqual(updated[0]["settled_by"], "corvin-k1")
        self.assertEqual(flips[0]["new_status"], "corrected")

    def test_saw_high_confirms_a_true_rumor(self):
        updated, flips = reconcile_rumors(
            [self._record("true")], [self._truth()])
        self.assertEqual(updated[0]["status"], "confirmed")
        self.assertEqual(flips[0]["new_status"], "confirmed")

    def test_heard_high_does_not_correct(self):
        updated, flips = reconcile_rumors(
            [self._record("false")],
            [self._truth(source="heard_from")])
        self.assertEqual(updated[0]["status"], "open")
        self.assertEqual(flips, [])

    def test_saw_medium_does_not_correct(self):
        updated, flips = reconcile_rumors(
            [self._record("false")],
            [self._truth(confidence="medium")])
        self.assertEqual(updated[0]["status"], "open")
        self.assertEqual(flips, [])

    def test_unknown_rumor_never_flips(self):
        updated, flips = reconcile_rumors(
            [self._record("unknown")], [self._truth()])
        self.assertEqual(updated[0]["status"], "open")
        self.assertEqual(flips, [])

    def test_unrelated_topic_does_not_flip(self):
        updated, flips = reconcile_rumors(
            [self._record("false")],
            [self._truth(topic="moonpetal")])
        self.assertEqual(updated[0]["status"], "open")
        self.assertEqual(flips, [])

    def test_settled_record_does_not_flip_again(self):
        updated, flips = reconcile_rumors(
            [self._record("false", status="corrected")],
            [self._truth()])
        self.assertEqual(updated[0]["status"], "corrected")
        self.assertEqual(flips, [])

    def test_no_truth_grade_learned_changes_nothing(self):
        updated, flips = reconcile_rumors([self._record("false")], [])
        self.assertEqual(updated[0]["status"], "open")
        self.assertEqual(flips, [])


if __name__ == "__main__":
    unittest.main()
