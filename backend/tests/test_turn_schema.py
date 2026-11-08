"""Slice 1 tests: the explicit turn schema and the unified challenge format.

Covers the plan's verification list: the prompt's own quest shape
validates, the engine assigns every DC, severe failure withdraws a quest,
accepting a quest that was never added is reported, a full turn validates
through TurnResponse, and a JSON-mode turn parses with no regex involved.
"""
import asyncio
import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world
from backend.gemini_service import (
    CHALLENGE_SYSTEM_PROMPT,
    assign_dc,
    get_structured_turn,
    validate_quest_output,
    validate_skill_check_proposal,
)
from backend.models import TurnResponse
from pydantic import ValidationError


# The quest shape the rewritten challenge prompt shows the model: lowercase
# types, a tier per challenge, no "dc" anywhere.
PROMPT_EXAMPLE_QUEST = {
    "id": "quest_id_snake_case",
    "description": "Brief description of the task.",
    "giver_npc": "Elara",
    "tier": "average",
    "accept_response": "Great, I knew I could count on you.",
    "refuse_response": "A pity. I'll find someone else.",
    "challenges": [
        {
            "id": "challenge_id_snake_case",
            "type": "strength",
            "tier": "average",
            "description": "Lift the heavy beam."
        }
    ],
    "involved_entities": ["Entity Name 1", "Entity Name 2"],
}


class TestTurnSchema(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_turn_schema"
        self.gsm.create_session(self.session_id, "TestPlayer")

    def test_prompt_example_quest_validates(self):
        # The prompt's own example must pass its own validator — the §11
        # break was exactly that it did not.
        self.assertEqual(validate_quest_output(PROMPT_EXAMPLE_QUEST), [])

    def test_engine_assigns_dc_in_band(self):
        # A model-format quest (no dc) validates, and the engine assigns a
        # DC inside the tier's band.
        self.assertEqual(validate_quest_output(PROMPT_EXAMPLE_QUEST), [])
        for _ in range(50):
            self.assertIn(assign_dc(tier="average"), range(10, 13))
            self.assertIn(assign_dc(tier="tough"), range(15, 18))
            self.assertIn(assign_dc(difficulty="medium"), range(15, 18))
            self.assertIn(assign_dc(difficulty="heroic"), range(22, 26))
        # Modifiers move the DC (knowledge making a check easier).
        self.assertLessEqual(assign_dc(tier="tough", modifiers=[-5]), 12)

    def test_label_only_skill_check_validates(self):
        proposal = {
            "type": "charisma",
            "difficulty": "medium",
            "description": "Persuade Corvin to hear you out.",
            "success_response": "Hm. Sit.",
            "failure_response": "No. Go back.",
        }
        self.assertEqual(validate_skill_check_proposal(proposal), [])
        dc = assign_dc(difficulty=proposal["difficulty"])
        self.assertIn(dc, range(15, 18))
        # An unknown label is an error, not a silent default.
        bad = dict(proposal, difficulty="impossible")
        self.assertTrue(len(validate_skill_check_proposal(bad)) > 0)

    def test_full_turn_validates(self):
        turn = TurnResponse.model_validate({
            "dialogue": "Oh, thank you, Traveler. Truly.",
            "quest_accepted": "moonpetal_poacher",
            "claims": [
                {"text": "Corvin camps in the Whispering Woods.",
                 "kind": "fact", "subject": "Corvin"},
                {"text": "He takes the roots because he needs the coin.",
                 "kind": "inference", "subject": "Corvin"},
            ],
            "referrals": [
                {"to_name": "Joren", "to_kind": "person",
                 "reason": "He has seen the man."}
            ],
            "new_entities": [
                {"kind": "character", "name": "Joren",
                 "description": "A trapper.", "location": "Oakhaven"}
            ],
            "memory_update": ["The player promised to help with the moonpetal."],
        })
        self.assertEqual(turn.dialogue, "Oh, thank you, Traveler. Truly.")
        self.assertEqual(len(turn.claims), 2)
        # An invalid difficulty label fails the whole turn (which the
        # engine then falls back to dialogue-only, not a crash).
        with self.assertRaises(ValidationError):
            TurnResponse.model_validate({
                "dialogue": "Hello.",
                "skill_check": {
                    "type": "charisma",
                    "difficulty": "impossible",
                    "description": "Persuade.",
                    "success_response": "Yes.",
                    "failure_response": "No.",
                },
            })

    def test_unknown_entity_kind_coerces_instead_of_voiding_turn(self):
        # Verification replay caught a model writing "thing" for a flower:
        # under strict validation the whole turn failed and a perfect quest
        # offer was lost. Taxonomy coerces (with a warning); mechanics
        # (type, tier, difficulty) stay strict.
        turn = TurnResponse.model_validate({
            "dialogue": "The moonpetal grows here.",
            "new_entities": [
                {"kind": "thing", "name": "moonpetal",
                 "description": "A silver-white flower."},
            ],
        })
        self.assertEqual(turn.new_entities[0].kind, "other")
        self.assertEqual(turn.dialogue, "The moonpetal grows here.")

    def test_accept_missing_quest_is_reported(self):
        # The Turn 2 knock-on: accepting a quest that was never added used
        # to silently do nothing. Now it returns None.
        self.assertIsNone(self.gsm.accept_quest(self.session_id, "no_such_quest"))

    def test_severe_failure_withdraws_quest(self):
        quest_data = {
            "id": "test_quest_severe",
            "description": "Severe Test",
            "giver_npc": "TestGiver",
            "challenges": [
                {
                    "id": "test_chal_severe",
                    "type": "strength",
                    "dc": 15,
                    "description": "Lift the beam."
                }
            ]
        }
        self.gsm.add_quest(self.session_id, quest_data)
        # Roll 5 + modifier 0 = 5 vs DC 15: missed by 10, severe.
        with patch('random.randint', return_value=5):
            result = self.gsm.resolve_challenge(self.session_id, "test_chal_severe")
        self.assertFalse(result['success'])
        self.assertEqual(result.get('severity'), 'severe')
        conn = sqlite3.connect('backend/game_data.db')
        row = conn.execute(
            "SELECT status FROM quests WHERE id = ? AND session_id = ?",
            ("test_quest_severe", self.session_id)).fetchone()
        conn.close()
        self.assertEqual(row[0], "withdrawn")

    def test_prompt_formats_with_bindings_and_band(self):
        # The prompt main.py builds must fill every placeholder — a leaked
        # "{bindings}" would reach the model as literal text.
        filled = CHALLENGE_SYSTEM_PROMPT.format(
            player_stats='{}', npc_state='{}', failure_severity='None',
            resource_level='poor', can_offer='gratitude',
            cannot_offer='gold', bindings='BINDINGS',
            rollable_band='BAND')
        self.assertIn('BINDINGS', filled)
        self.assertIn('BAND', filled)
        self.assertNotIn('{bindings}', filled)
        self.assertNotIn('{rollable_band}', filled)
        self.assertNotIn('{resource_level}', filled)


class TestStructuredTurn(unittest.IsolatedAsyncioTestCase):
    async def test_json_mode_turn_parses_without_regex(self):
        # A JSON-mode reply parses straight into TurnResponse — no regex
        # extraction involved in the success path.
        turn_json = json.dumps({
            "dialogue": "You found my camp. Most don't.",
            "skill_check": {
                "type": "charisma",
                "difficulty": "medium",
                "description": "Persuade Corvin to hear you out.",
                "success_response": "Hm. Sit.",
                "failure_response": "No. Go back.",
            },
            "claims": [{"text": "Corvin camps here.", "kind": "fact",
                        "subject": "Corvin"}],
            "memory_update": ["The player came to talk, not fight."],
        })
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(return_value=turn_json)):
            turn = await get_structured_turn([], system_instruction="test")
        self.assertIsInstance(turn, TurnResponse)
        self.assertEqual(turn.dialogue, "You found my camp. Most don't.")
        self.assertEqual(turn.skill_check.difficulty, "medium")
        self.assertEqual(len(turn.claims), 1)

    async def test_bad_turn_falls_back_to_dialogue(self):
        # A turn that fails validation still plays — dialogue only, no
        # structured effects, no exception.
        bad_json = json.dumps({
            "dialogue": "Hello, traveler.",
            "skill_check": {
                "type": "charisma",
                "difficulty": "impossible",
                "description": "Persuade.",
                "success_response": "Yes.",
                "failure_response": "No.",
            },
        })
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(return_value=bad_json)):
            turn = await get_structured_turn([], system_instruction="test")
        self.assertEqual(turn.dialogue, "Hello, traveler.")
        self.assertIsNone(turn.skill_check)


class TestInteractEndToEnd(unittest.TestCase):
    """The wired path: /command "talk to Elara" then two /interact turns
    against a mocked provider, replaying playtest Turns 1–2. Turn 1's offer
    must validate under the new contract and land with an engine-assigned
    DC; Turn 2's acceptance must actually activate the quest (the §12
    silent-accept regression)."""

    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_e2e"
        self.gsm.create_session(self.session_id, "TestPlayer")
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)

    def test_offer_then_accept_end_to_end(self):
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Elara"})
        self.assertEqual(talk.status_code, 200)
        # The /command response's conversation_partner_name reflects the
        # pre-command state (a pre-existing quirk), so check the session.
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id), "Elara")

        turn1 = json.dumps({
            "dialogue": "Corvin has been stripping the moonpetal bare in the "
                        "Whispering Woods, roots and all. I need it for the "
                        "remedies Oakhaven relies on.",
            "quest_offered": {
                "id": "moonpetal_poacher",
                "description": "Stop Corvin the poacher and bring back moonpetal, roots intact.",
                "tier": "average",
                "accept_response": "Oh, thank you, Traveler. Truly.",
                "refuse_response": "A pity. The town will feel it.",
                "challenges": [
                    {"id": "track_corvin", "type": "dexterity",
                     "tier": "average", "description": "Track Corvin through the woods."},
                    {"id": "persuade_corvin", "type": "charisma",
                     "tier": "tough", "description": "Persuade Corvin to stop."},
                ],
                "involved_entities": ["Corvin", "Whispering Woods", "moonpetal"],
            },
            "claims": [{"text": "Corvin strips the moonpetal bare.",
                        "kind": "fact", "subject": "Corvin"}],
        })
        turn2 = json.dumps({
            "dialogue": "Oh, thank you, Traveler. Truly. Take the north path "
                        "out of Oakhaven and follow the stream into the woods.",
            "quest_accepted": "moonpetal_poacher",
        })

        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(side_effect=[turn1, turn2])):
            r1 = self.client.post("/interact", json={
                "session_id": self.session_id,
                "message": "Do you have any work?"})
            self.assertEqual(r1.status_code, 200)
            self.assertIn("moonpetal", r1.json()["dialogue"].lower())

            # The offer landed with engine-assigned DCs inside the bands.
            conn = sqlite3.connect('backend/game_data.db')
            rows = conn.execute(
                "SELECT id, dc FROM challenges WHERE quest_id = ?",
                ("moonpetal_poacher",)).fetchall()
            conn.close()
            self.assertEqual(len(rows), 2)
            dcs = {row[0]: row[1] for row in rows}
            self.assertIn(dcs["track_corvin"], range(10, 13))
            self.assertIn(dcs["persuade_corvin"], range(15, 18))

            r2 = self.client.post("/interact", json={
                "session_id": self.session_id,
                "message": "I'll do it. I'll find Corvin and bring back the moonpetal."})
            self.assertEqual(r2.status_code, 200)

        # Turn 2's acceptance actually activated the quest this time.
        conn = sqlite3.connect('backend/game_data.db')
        status = conn.execute(
            "SELECT status FROM quests WHERE id = ? AND session_id = ?",
            ("moonpetal_poacher", self.session_id)).fetchone()[0]
        conn.close()
        self.assertEqual(status, "active")


if __name__ == '__main__':
    unittest.main()
