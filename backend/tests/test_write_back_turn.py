"""Slice 5 end-to-end: the write-back through the real /interact
endpoint, with a scripted provider.

A turn proposing a new character must come back with that character
in metadata["write_back"]["committed"], present in the loaded world,
and persisted in the world file. A turn proposing an item or an
already-existing person must not commit anything. All of it runs
against a scratch world (TestWriteback.json, a copy of Elodia whose
"world" field is renamed, so the default save path is the scratch
file), never the shipped file.
"""
import json
import os
import shutil
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_state_manager import GameStateManager
from backend.game_world import game_world, initialize_game_world


class TestWriteBackTurn(unittest.TestCase):
    def setUp(self):
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestWriteback.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        # Rename the world inside the file, so the default save path
        # (derived from the world name) is the scratch file.
        with open(self.scratch) as f:
            data = json.load(f)
        data["world"] = "TestWriteback"
        with open(self.scratch, "w") as f:
            json.dump(data, f, indent=4)
        initialize_game_world("TestWriteback")
        self.gsm = GameStateManager()
        self.session_id = "test_session_write_back_turn"
        self.gsm.create_session(self.session_id, "TestPlayer")
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Elara"})
        self.assertEqual(talk.status_code, 200)
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id),
                         "Elara")

    def tearDown(self):
        if os.path.exists(self.scratch):
            os.remove(self.scratch)
        tmp = self.scratch + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        initialize_game_world("Elodia")

    def _interact(self, message, replies):
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(side_effect=replies)):
            r = self.client.post("/interact", json={
                "session_id": self.session_id, "message": message})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_new_character_commits_and_persists(self):
        turn = json.dumps({
            "dialogue": "There's a trapper named Joren who's seen the man.",
            "new_entities": [
                {"kind": "character", "name": "Joren",
                 "description": "A trapper who has seen the man.",
                 "location": "Oakhaven"},
                {"kind": "other", "name": "moonpetal",
                 "description": "A pale flower that blooms at night."},
            ],
        })
        body = self._interact("Who else has seen him?", [turn])
        write_back = body["metadata"]["write_back"]
        self.assertEqual(len(write_back["committed"]), 1)
        self.assertEqual(write_back["committed"][0]["name"], "Joren")
        self.assertEqual(write_back["committed"][0]["id"], "joren")
        self.assertEqual(len(write_back["uncommitted"]), 1)
        self.assertEqual(write_back["uncommitted"][0]["name"], "moonpetal")
        self.assertFalse(write_back["persistence_failed"])

        joren = game_world.get_character("Joren", "Oakhaven")
        self.assertIsNotNone(joren)
        self.assertEqual(joren.provenance["source"], "generated")
        self.assertEqual(joren.provenance["npc"], "Elara")

        with open(self.scratch) as f:
            saved = json.load(f)
        names = [c["name"] for loc in saved["locations"]
                 for c in loc["characters"]]
        self.assertIn("Joren", names)
        self.assertNotIn("The Architect", names)

    def test_duplicate_not_committed(self):
        turn = json.dumps({
            "dialogue": "Brom is new here, you know.",
            "new_entities": [
                {"kind": "character", "name": "Brom",
                 "description": "Another smith.", "location": "Oakhaven"},
            ],
        })
        body = self._interact("Tell me about Brom.", [turn])
        write_back = body["metadata"]["write_back"]
        self.assertEqual(write_back["committed"], [])
        self.assertEqual(len(write_back["skipped"]), 1)
        self.assertIn("Duplicate", write_back["skipped"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
