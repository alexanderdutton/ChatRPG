"""Slice 5 tests: the write-back / commit bridge.

The commit takes the turn's structured `new_entities` and writes the
committable ones into the loaded world — canon with provenance — and
persists the world file. Characters and locations commit; items and
"other" do not (no world model yet). Duplicates and near-duplicates
are skipped, never forked. All tests run against a scratch world
(TestWriteback.json, a copy of Elodia), never the shipped file, and
the world is re-initialized to Elodia afterwards.
"""
import json
import os
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_world import game_world, initialize_game_world
from backend.models import NewEntity, TurnResponse
from backend.write_back import commit_new_entities


def _turn(*entities):
    return TurnResponse(
        dialogue="A turn.",
        new_entities=[NewEntity(**e) for e in entities],
    )


class TestWriteBack(unittest.TestCase):
    def setUp(self):
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestWriteback.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        initialize_game_world("TestWriteback")

    def tearDown(self):
        if os.path.exists(self.scratch):
            os.remove(self.scratch)
        tmp = self.scratch + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        initialize_game_world("Elodia")

    def _commit(self, turn, **kwargs):
        kwargs.setdefault("session_id", "test_session_write_back")
        kwargs.setdefault("npc_name", "Elara")
        kwargs.setdefault("current_location", "Oakhaven")
        kwargs.setdefault("npc_x", 2)
        kwargs.setdefault("npc_y", 3)
        kwargs.setdefault("turn_number", 1)
        # The loaded world's name comes from the file's content
        # ("Elodia"), not its filename, so the default save path would
        # be the shipped file — point the save at the scratch file.
        kwargs.setdefault("world_file", self.scratch)
        return commit_new_entities(turn, **kwargs)

    def test_character_commits_with_defaults_and_provenance(self):
        turn = _turn({"kind": "character", "name": "Joren",
                      "description": "A trapper who has seen the man.",
                      "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(len(result.committed), 1)
        self.assertEqual(result.committed[0]["kind"], "character")
        self.assertEqual(result.committed[0]["id"], "joren")
        self.assertEqual(result.committed[0]["location"], "Oakhaven")
        self.assertFalse(result.persistence_failed)

        joren = game_world.get_character("Joren", "Oakhaven")
        self.assertIsNotNone(joren)
        self.assertEqual(joren.race, "unknown")
        self.assertEqual(joren.occupation, "unknown")
        self.assertEqual(joren.x, 2)
        self.assertEqual(joren.y, 3)
        self.assertIn("trapper", joren.personality_prompt)
        prov = joren.provenance
        self.assertEqual(prov["source"], "generated")
        self.assertEqual(prov["session_id"], "test_session_write_back")
        self.assertEqual(prov["turn"], 1)
        self.assertEqual(prov["npc"], "Elara")
        self.assertEqual(prov["origin"], "model-invented")
        self.assertIn("race", prov["engine_defaults"])

    def test_player_mentioned_origin(self):
        turn = _turn({"kind": "character", "name": "Joren",
                      "description": "A trapper.", "location": "Oakhaven"})
        self._commit(turn, player_message="Have you seen Joren lately?")
        joren = game_world.get_character("Joren", "Oakhaven")
        self.assertEqual(joren.provenance["origin"], "player-mentioned")

    def test_duplicate_name_skipped(self):
        turn = _turn({"kind": "character", "name": "Brom",
                      "description": "Another smith.", "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(result.committed, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("Duplicate", result.skipped[0]["reason"])
        # The world still holds exactly one Brom.
        self.assertIsNotNone(game_world.get_character("Brom", "Oakhaven"))

    def test_near_duplicate_skipped(self):
        # "Old Elara" shares the whole word "Elara" with the existing
        # Elara — the engine skips rather than forking an identity.
        turn = _turn({"kind": "character", "name": "Old Elara",
                      "description": "An elder.", "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(result.committed, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("Possible duplicate", result.skipped[0]["reason"])

    def test_location_commits_as_stub(self):
        turn = _turn({"kind": "location", "name": "Deepwood",
                      "description": "A dark forest.",
                      "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(len(result.committed), 1)
        self.assertEqual(result.committed[0]["kind"], "location")
        deepwood = game_world.get_location("Deepwood")
        self.assertIsNotNone(deepwood)
        self.assertEqual(deepwood.exits, {})
        self.assertEqual(deepwood.provenance["proposed_connection"],
                         "Oakhaven")
        self.assertIn("raw_layout", deepwood.provenance["engine_defaults"])

    def test_item_and_other_not_committed(self):
        # Turn 1's moonpetal, exactly: kind "other", a proposal only.
        turn = _turn({"kind": "other", "name": "moonpetal",
                      "description": "A pale flower that blooms at night."},
                     {"kind": "item", "name": "rusty key",
                      "description": "A key.", "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(result.committed, [])
        self.assertEqual(len(result.uncommitted), 2)
        self.assertIn("No world model", result.uncommitted[0]["reason"])
        self.assertIsNone(game_world.get_location("moonpetal"))

    def test_save_round_trip_and_architect_excluded(self):
        turn = _turn({"kind": "character", "name": "Joren",
                      "description": "A trapper.", "location": "Oakhaven"})
        self._commit(turn)
        with open(self.scratch) as f:
            saved = json.load(f)
        names = [c["name"] for loc in saved["locations"]
                 for c in loc["characters"]]
        self.assertIn("Joren", names)
        self.assertNotIn("The Architect", names)
        joren = next(c for loc in saved["locations"]
                     for c in loc["characters"] if c["name"] == "Joren")
        self.assertEqual(joren["id"], "joren")
        self.assertEqual(joren["provenance"]["source"], "generated")
        # Shipped entities serialize exactly as before: no id/provenance.
        elara = next(c for loc in saved["locations"]
                     for c in loc["characters"] if c["name"] == "Elara")
        self.assertNotIn("id", elara)
        self.assertNotIn("provenance", elara)

    def test_save_failure_flagged_without_crashing(self):
        turn = _turn({"kind": "character", "name": "Joren",
                      "description": "A trapper.", "location": "Oakhaven"})
        with patch("backend.write_back.save_world",
                   side_effect=OSError("disk full")):
            result = self._commit(turn)
        self.assertTrue(result.persistence_failed)
        self.assertEqual(len(result.committed), 1)
        # In-memory commit stands for the session.
        self.assertIsNotNone(game_world.get_character("Joren", "Oakhaven"))

    def test_missing_description_defaulted(self):
        turn = _turn({"kind": "character", "name": "Joren",
                      "location": "Oakhaven"})
        result = self._commit(turn)
        self.assertEqual(len(result.committed), 1)
        joren = game_world.get_character("Joren", "Oakhaven")
        self.assertTrue(joren.description.startswith("A character named"))
        self.assertIn("description", joren.provenance["engine_defaults"])


if __name__ == "__main__":
    unittest.main()
