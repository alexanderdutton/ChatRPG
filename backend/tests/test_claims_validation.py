"""Slice 4 tests: the validation invariant.

The validator is a pure function of (turn, grounded set), so most
cases use a hand-built set mirroring the shipped world (Oakhaven;
Brom and Elara present; exits north to the Whispering Woods, south to
Riverford). A Corvin variant stands in for a world entity the model
was not shown this turn. Two tests build the grounded set for real,
from a session, to pin what the engine actually shows the model.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.claims_validation import (
    GroundedSet,
    MISMATCH_POLICY,
    add_tool_results,
    build_grounded_set,
    build_prompt_grounded_set,
    validate_turn,
)
from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world
from backend.models import TurnResponse


def _grounded(**overrides):
    """The shipped world as the model sees it this turn: Oakhaven,
    Brom and Elara present, both exit destinations named. Corvin is
    known to the world (in the Woods) but NOT shown — the ungrounded
    variant. Callers override fields per case."""
    base = {
        "names": {"elara": "Elara", "brom": "Brom", "oakhaven": "Oakhaven",
                  "whispering woods": "Whispering Woods",
                  "riverford": "Riverford",
                  "market square": "Market Square", "smithy": "Smithy"},
        "missed_lookups": {},
        "character_locations": {"elara": "Oakhaven", "brom": "Oakhaven",
                                "corvin": "Whispering Woods"},
        "location_names": {"oakhaven": "Oakhaven",
                           "whispering woods": "Whispering Woods",
                           "riverford": "Riverford"},
        "known_names": {"elara": "Elara", "brom": "Brom",
                        "oakhaven": "Oakhaven",
                        "whispering woods": "Whispering Woods",
                        "riverford": "Riverford",
                        "market square": "Market Square",
                        "smithy": "Smithy", "corvin": "Corvin"},
        "current_location": "Oakhaven",
        "exits": {"north": "Whispering Woods", "south": "Riverford"},
    }
    base.update(overrides)
    return GroundedSet(**base)


def _turn(dialogue, claims=None, **extra):
    out = {"dialogue": dialogue, "claims": claims or []}
    out.update(extra)
    return TurnResponse.model_validate(out)


def _claim(text, kind="fact", subject=None):
    return {"text": text, "kind": kind, "subject": subject}


class TestClaimVerdicts(unittest.TestCase):
    def test_grounded_claim_passes(self):
        turn = _turn("Brom is at the smithy.",
                     [_claim("Brom works at the smithy in Oakhaven.",
                             subject="Brom")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "grounded")
        self.assertEqual(v.claims[0].action, "accept")
        self.assertEqual(v.correction_notes, [])

    def test_location_contradiction_corrects(self):
        turn = _turn("Brom moved his work south.",
                     [_claim("Brom works at the smithy in Riverford.",
                             subject="Brom")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        entry = v.claims[0]
        self.assertEqual(entry.verdict, "contradicts")
        self.assertEqual(entry.action, "correct")
        self.assertIn("Oakhaven", entry.evidence)
        self.assertEqual(len(v.correction_notes), 1)
        self.assertIn("Oakhaven", v.correction_notes[0])
        self.assertIn("Riverford", v.correction_notes[0])

    def test_rumor_contradiction_is_allowed_wrong(self):
        turn = _turn("Folks say Brom moved south.",
                     [_claim("Brom works at the smithy in Riverford.",
                             kind="rumor", subject="Brom")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "contradicts")
        self.assertEqual(v.claims[0].action, "allow_wrong")
        self.assertEqual(v.correction_notes, [])
        self.assertEqual(MISMATCH_POLICY[("contradicts", "rumor")],
                         "allow_wrong")

    def test_exit_contradiction(self):
        turn = _turn("Take the north road to Riverford.",
                     [_claim("The north exit leads to Riverford.",
                             subject="Riverford")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "contradicts")
        self.assertEqual(v.claims[0].action, "correct")
        self.assertIn("Whispering Woods", v.correction_notes[0])

    def test_correct_exit_passes(self):
        turn = _turn("Take the north road into the woods.",
                     [_claim("The north exit leads to the Whispering Woods.",
                             subject="Whispering Woods")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "grounded")
        self.assertEqual(v.correction_notes, [])

    def test_tool_miss_negative_is_verified_absent(self):
        grounded = _grounded(missed_lookups={"wizard's tower": "Wizard's Tower"})
        turn = _turn("I know of no such place.",
                     [_claim("No place called the Wizard's Tower exists "
                             "in Elodia.", subject="Wizard's Tower")])
        v = validate_turn(turn, grounded, npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "verified_absent")
        self.assertEqual(v.claims[0].action, "accept")
        self.assertEqual(v.correction_notes, [])

    def test_new_name_is_proposed_not_committed(self):
        turn = _turn("Millbrook is a village down the road.",
                     [_claim("Millbrook is a village down the road.",
                             subject="Millbrook")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "new_proposed")
        self.assertEqual(v.claims[0].action, "propose")
        self.assertEqual(v.proposals, ["Millbrook"])
        self.assertEqual(v.correction_notes, [])

    def test_ungrounded_world_entity_is_noted_not_corrected(self):
        turn = _turn("Corvin owes me money.",
                     [_claim("Corvin owes me money.", subject="Corvin")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "ungrounded")
        self.assertEqual(v.claims[0].action, "note")
        self.assertEqual(v.correction_notes, [])

    def test_ungrounded_entity_location_contradiction(self):
        turn = _turn("Corvin camps down south.",
                     [_claim("Corvin camps in Riverford.", subject="Corvin")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "contradicts")
        self.assertEqual(v.claims[0].action, "correct")

    def test_origin_phrasing_is_not_a_contradiction(self):
        # "Came from Riverford" is an origin, not a location claim.
        turn = _turn("Brom came from Riverford years ago.",
                     [_claim("Brom came from Riverford years ago.",
                             subject="Brom")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "grounded")

    def test_descriptive_subject_is_unverifiable(self):
        turn = _turn("The blacksmith is grumpy today.",
                     [_claim("The blacksmith is grumpy today.",
                             subject="the blacksmith")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "unverifiable")
        self.assertEqual(v.claims[0].action, "accept")

    def test_no_subject_is_unverifiable(self):
        turn = _turn("The moonpetal is disappearing.",
                     [_claim("The moonpetal is disappearing.")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "unverifiable")

    def test_dialogue_scan_catches_ungrounded_name(self):
        turn = _turn("I saw Corvin near the old mill.")
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        self.assertEqual(len(v.dialogue_mentions), 1)
        self.assertEqual(v.dialogue_mentions[0]["name"], "Corvin")

    def test_dialogue_scan_ignores_grounded_and_declared(self):
        turn = _turn("Brom is at the smithy in Oakhaven, and Corvin "
                     "strips the moonpetal bare.",
                     [_claim("Corvin strips the moonpetal bare.",
                             subject="Corvin")])
        v = validate_turn(turn, _grounded(), npc_name="Elara")
        # Brom and Oakhaven are grounded; Corvin is declared in claims.
        self.assertEqual(v.dialogue_mentions, [])


class TestGroundedSetFromSession(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_claims_validation"
        self.gsm.create_session(self.session_id, "TestPlayer")

    def test_bindings_ground_the_local_world(self):
        grounded = build_prompt_grounded_set(self.session_id, "Elara")
        for name in ("elara", "brom", "oakhaven", "riverford",
                     "whispering woods", "market square", "smithy"):
            self.assertIn(name, grounded.names, name)
        self.assertEqual(grounded.current_location, "Oakhaven")
        self.assertEqual(grounded.character_locations.get("brom"), "Oakhaven")
        self.assertNotIn("corvin", grounded.names)

    def test_tool_miss_grounds_the_negative(self):
        grounded = build_grounded_set(
            self.session_id, "Elara",
            [{"tool": "locate_entity", "args": {"name": "Wizard's Tower"},
              "result": {"found": False, "query": "Wizard's Tower",
                         "message": "No place or person called "
                                    "'Wizard's Tower' exists.",
                         "near_matches": []}}])
        self.assertIn("wizard's tower", grounded.missed_lookups)
        turn = _turn("I know of no such place.",
                     [_claim("No place called the Wizard's Tower exists "
                             "in Elodia.", subject="Wizard's Tower")])
        v = validate_turn(turn, grounded, npc_name="Elara")
        self.assertEqual(v.claims[0].verdict, "verified_absent")

    def test_tool_hit_grounds_the_name(self):
        grounded = build_prompt_grounded_set(self.session_id, "Elara")
        add_tool_results(grounded, [
            {"tool": "locate_entity", "args": {"name": "Brom"},
             "result": {"found": True, "kind": "character", "name": "Brom",
                        "where": "Oakhaven at [1, 2]",
                        "occupation": "Blacksmith", "description": ""}}])
        self.assertIn("brom", grounded.names)
        self.assertEqual(grounded.missed_lookups, {})


if __name__ == '__main__':
    unittest.main()
