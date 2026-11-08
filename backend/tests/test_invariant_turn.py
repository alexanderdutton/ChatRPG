"""Slice 4 end-to-end: the validation invariant through the real
/interact endpoint, with a scripted provider.

A turn asserting a false location for a known NPC must come back with
a contradicts verdict and a [System] correction note in the
conversation history, so the NPC can self-correct next turn. A turn
whose tool lookup missed must come back verified_absent, not
new_proposed. A grounded turn must come back clean.
"""
import json
import os
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


class TestInvariantTurn(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_invariant_turn"
        self.gsm.create_session(self.session_id, "TestPlayer")
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Elara"})
        self.assertEqual(talk.status_code, 200)
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id),
                         "Elara")

    def _interact(self, message, replies):
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(side_effect=replies)):
            r = self.client.post("/interact", json={
                "session_id": self.session_id, "message": message})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_false_location_is_corrected_in_history(self):
        turn = json.dumps({
            "dialogue": "Brom? He's moved his work to Riverford, down south.",
            "claims": [{"text": "Brom works at the smithy in Riverford.",
                        "kind": "fact", "subject": "Brom"}],
        })
        body = self._interact("Where is Brom working these days?", [turn])
        validation = body["metadata"]["claims_validation"]
        self.assertEqual(validation["claims"][0]["verdict"], "contradicts")
        self.assertEqual(validation["claims"][0]["action"], "correct")
        self.assertEqual(len(validation["correction_notes"]), 1)

        # The correction note is the last thing in the history, so the
        # model sees it next turn.
        history = self.gsm.get_conversation_history(self.session_id)
        last = history[-1]["parts"][0]
        self.assertIn("[System] Correction", last)
        self.assertIn("Oakhaven", last)
        self.assertIn("Riverford", last)

    def test_tool_miss_negative_is_verified_absent(self):
        tool_calls = json.dumps({
            "tool_calls": [{"tool": "locate_entity",
                            "args": {"name": "Wizard's Tower"}}]})
        turn = json.dumps({
            "dialogue": "I know of no such place, traveler. What did "
                        "you hear?",
            "claims": [{"text": "No place called the Wizard's Tower "
                                "exists in Elodia.",
                        "kind": "fact", "subject": "Wizard's Tower"}],
        })
        body = self._interact("Is there a wizard's tower in Elodia?",
                              [tool_calls, turn])
        validation = body["metadata"]["claims_validation"]
        self.assertEqual(validation["claims"][0]["verdict"],
                         "verified_absent")
        self.assertEqual(validation["correction_notes"], [])

    def test_grounded_turn_comes_back_clean(self):
        turn = json.dumps({
            "dialogue": "Brom is at the smithy in Oakhaven, as always.",
            "claims": [{"text": "Brom works at the smithy in Oakhaven.",
                        "kind": "fact", "subject": "Brom"}],
        })
        body = self._interact("Where is Brom?", [turn])
        validation = body["metadata"]["claims_validation"]
        self.assertEqual(validation["claims"][0]["verdict"], "grounded")
        self.assertEqual(validation["correction_notes"], [])
        self.assertEqual(validation["dialogue_mentions"], [])
        self.assertEqual(validation["proposals"], [])


if __name__ == '__main__':
    unittest.main()
