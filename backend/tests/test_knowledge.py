"""Slice 6 unit tests: knowledge as a mechanic.

The modifier computation is a pure function of (ledger, subjects,
check type, description); the ledger and sharing rules are pure too.
World-state checks (seeded knowledge, save round-trip) run against
the shipped Elodia file, read-only.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_world import game_world, initialize_game_world
from backend.knowledge import (
    knowledge_modifiers,
    learn_knowledge,
    validate_shared,
)
from backend.models import KnowledgeEntry


BRIEFING = {"id": "elara-k5", "topic": "Corvin",
            "fact": "What the taking costs the town.",
            "source": "saw", "confidence": "high",
            "dc_modifier": -5, "applies_to": "charisma"}
JOREN_KNOWLEDGE = {"id": "joren-k1", "topic": "Corvin",
                   "fact": "He takes the roots for his sick wife.",
                   "source": "saw", "confidence": "high",
                   "dc_modifier": -5, "applies_to": "charisma"}
MOONPETAL_FACT = {"id": "elara-k1", "topic": "moonpetal",
                  "fact": "The moonpetal is disappearing.",
                  "source": "saw", "confidence": "high",
                  "dc_modifier": 0}


class TestKnowledgeModifiers(unittest.TestCase):
    def test_exact_topic_match(self):
        self.assertEqual(
            knowledge_modifiers([BRIEFING], subjects=["Corvin"],
                                check_type="charisma"),
            [-5])

    def test_subject_containing_topic_matches(self):
        self.assertEqual(
            knowledge_modifiers([BRIEFING],
                                subjects=["Corvin the poacher"],
                                check_type="charisma"),
            [-5])

    def test_description_fallback_without_subject(self):
        self.assertEqual(
            knowledge_modifiers([BRIEFING], subjects=None,
                                check_type="charisma",
                                description="Persuade Corvin to stop."),
            [-5])

    def test_applies_to_filters_by_type(self):
        # The briefing is persuasion knowledge; it does not help a
        # dexterity check about the same person.
        self.assertEqual(
            knowledge_modifiers([BRIEFING], subjects=["Corvin"],
                                check_type="dexterity"),
            [])

    def test_unrelated_topic_does_not_match(self):
        self.assertEqual(
            knowledge_modifiers([BRIEFING], subjects=["Brom"],
                                check_type="charisma"),
            [])

    def test_multiple_entries_sum(self):
        # The worked example, both halves: Elara's briefing and
        # Joren's knowledge together are worth -10.
        self.assertEqual(
            knowledge_modifiers([BRIEFING, JOREN_KNOWLEDGE],
                                subjects=["Corvin"],
                                check_type="charisma"),
            [-5, -5])

    def test_entry_matches_once_across_subjects(self):
        self.assertEqual(
            knowledge_modifiers([BRIEFING],
                                subjects=["Corvin", "Corvin the poacher"],
                                check_type="charisma"),
            [-5])

    def test_zero_modifiers_excluded(self):
        self.assertEqual(
            knowledge_modifiers([MOONPETAL_FACT], subjects=["moonpetal"],
                                check_type="intelligence"),
            [])

    def test_whole_word_matching(self):
        # Ellie review, finding 4: a topic must appear as a whole
        # word, not as letters inside a longer word. "Oakhaven"
        # fires on "Oakhavenwood" under substring matching; it must
        # not here.
        oakhaven = {"id": "brom-k2", "topic": "Oakhaven",
                    "fact": "Brom knows every hinge in town.",
                    "source": "saw", "confidence": "high",
                    "dc_modifier": -5, "applies_to": None}
        self.assertEqual(
            knowledge_modifiers([oakhaven], subjects=None,
                                description="the Oakhavenwood forest"),
            [])
        # But a possessive is the same word, and a phrase still matches.
        self.assertEqual(
            knowledge_modifiers([BRIEFING], subjects=["Corvin's camp"],
                                check_type="charisma"),
            [-5])

    def test_scoped_entry_without_check_type_does_not_match(self):
        # Ellie review, finding 5: an entry scoped to a type, with no
        # type given, contributes nothing — and says so.
        with self.assertLogs("backend.knowledge", level="WARNING") as cm:
            self.assertEqual(
                knowledge_modifiers([BRIEFING], subjects=["Corvin"],
                                    check_type=None),
                [])
        self.assertTrue(any("elara-k5" in line for line in cm.output))


class TestSharingAndLedger(unittest.TestCase):
    def test_validate_shared_splits_known_and_unknown(self):
        valid, unknown = validate_shared(
            ["elara-k5", "nope-k9"], [BRIEFING, MOONPETAL_FACT])
        self.assertEqual([e["id"] for e in valid], ["elara-k5"])
        self.assertEqual(unknown, ["nope-k9"])

    def test_learn_dedupes_by_id(self):
        ledger, newly = learn_knowledge(
            [], [BRIEFING], learned_from="Elara", turn_number=1,
            session_id="s1")
        self.assertEqual(len(newly), 1)
        self.assertEqual(newly[0]["learned_from"], "Elara")
        self.assertEqual(newly[0]["turn"], 1)
        ledger, newly = learn_knowledge(
            ledger, [BRIEFING], learned_from="Elara", turn_number=2,
            session_id="s1")
        self.assertEqual(newly, [])
        self.assertEqual(len(ledger), 1)


class TestKnowledgeEntryValidation(unittest.TestCase):
    def test_modifier_clamped_not_rejected(self):
        entry = KnowledgeEntry(id="x", topic="Corvin", fact="f",
                               dc_modifier=25)
        self.assertEqual(entry.dc_modifier, 10)

    def test_unknown_source_coerced(self):
        entry = KnowledgeEntry(id="x", topic="Corvin", fact="f",
                               source="dreamed_it")
        self.assertEqual(entry.source, "heard_from")


class TestReferralIdResolution(unittest.TestCase):
    """Ellie review, finding 3: a referral recorded by name must
    still match a follow-up given by id — a generated NPC's id can be
    suffixed on collision ("brom-2"), so name-only matching would
    strand the referral forever."""

    def test_resolve_by_name_and_by_id(self):
        initialize_game_world("Elodia")
        from backend.models import Character
        second = Character(
            id="brom-2", name="Brom the Second", race="dwarf",
            occupation="blacksmith", description="Brom's cousin.",
            personality_prompt="You are Brom the Second.", x=1, y=1,
            short_description="Another dwarf blacksmith.")
        game_world.add_character(second, "Oakhaven")
        from backend.game_state_manager import GameStateManager
        gsm = GameStateManager()
        self.assertEqual(gsm.resolve_npc_id("Brom the Second"), "brom-2")
        self.assertEqual(gsm.resolve_npc_id("brom-2"), "brom-2")
        # Shipped NPCs have no id; the name is the identifier.
        self.assertEqual(gsm.resolve_npc_id("Elara"), "Elara")
        self.assertEqual(gsm.resolve_npc_id("Nobody"), "Nobody")

    def test_follow_up_by_id_matches_recorded_name(self):
        initialize_game_world("Elodia")
        from backend.models import Character
        second = Character(
            id="brom-2", name="Brom the Second", race="dwarf",
            occupation="blacksmith", description="Brom's cousin.",
            personality_prompt="You are Brom the Second.", x=1, y=1,
            short_description="Another dwarf blacksmith.")
        game_world.add_character(second, "Oakhaven")
        from backend.game_state_manager import GameStateManager
        gsm = GameStateManager()
        session_id = "test_session_referral_id"
        gsm.create_session(session_id, "TestPlayer")
        gsm.set_pending_referrals(session_id, [{
            "to_name": "Brom the Second",
            "to_id": gsm.resolve_npc_id("Brom the Second"),
            "to_kind": "person", "reason": "He knows the forge.",
            "from_npc": "Elara", "turn": 1, "followed": False,
        }])
        # The follow-up arrives as the slug id, not the name.
        self.assertTrue(gsm.mark_referral_followed(session_id, "brom-2"))
        self.assertTrue(
            gsm.get_pending_referrals(session_id)[0]["followed"])


class TestSeededKnowledge(unittest.TestCase):
    def test_elara_knows_what_section_14_says(self):
        initialize_game_world("Elodia")
        elara = game_world.get_character("Elara", "Oakhaven")
        by_id = {e.id: e for e in elara.knowledge}
        self.assertEqual(len(elara.knowledge), 5)
        briefing = by_id["elara-k5"]
        self.assertEqual(briefing.topic, "Corvin")
        self.assertEqual(briefing.dc_modifier, -5)
        self.assertEqual(briefing.applies_to, "charisma")
        self.assertEqual(briefing.source, "saw")
        self.assertEqual(briefing.confidence, "high")
        # The referral pointer is knowledge too, not a modifier.
        self.assertEqual(by_id["elara-k4"].topic, "Joren")
        self.assertEqual(by_id["elara-k4"].dc_modifier, 0)

    def test_brom_has_facts_not_modifiers(self):
        initialize_game_world("Elodia")
        brom = game_world.get_character("Brom", "Oakhaven")
        self.assertEqual(len(brom.knowledge), 2)
        self.assertTrue(all(e.dc_modifier == 0 for e in brom.knowledge))


if __name__ == "__main__":
    unittest.main()
