"""Slice 10 end-to-end: rumor spread through the real endpoints
and the manager tick.

A scratch world (TestRumors.json, a copy of the seeded Elodia
with Joren injected and Brom linked to Elara), never the shipped
file. The graph is a chain: Joren - Brom - Elara, Corvin
unconnected.

The rumor walks one hop per tick: Joren tells the player that
Corvin camps in the Whispering Woods (a false rumor -- the world
has Corvin in Oakhaven), the same turn's tick carries it to Brom,
the next tick to Elara, never to Corvin. Brom can then share it
with the player, who learns it at low confidence; the entry id is
per rumor, so the player learns it once no matter who tells them.
"""
import json
import os
import shutil
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_state_manager import GameStateManager
from backend.game_world import game_world, initialize_game_world


TEXT = "Corvin camps in the Whispering Woods."

JOREN = {
    "name": "Joren", "race": "Human", "occupation": "Trapper",
    "description": "A weathered trapper who knows the woods south of town.",
    "personality_prompt": "Joren is blunt and wary, and repeats what he hears.",
    "x": 2, "y": 2, "short_description": "A weathered trapper.",
    "connections": ["Brom"],
}

CORVIN = {
    "name": "Corvin", "race": "Human", "occupation": "Poacher",
    "description": "A proud, defensive man who takes moonpetal roots.",
    "personality_prompt": "Corvin is proud and defensive with strangers.",
    "x": 3, "y": 2, "short_description": "A proud poacher.",
}


class TestRumorSpreadTurn(unittest.TestCase):
    def setUp(self):
        # Import main first: it initializes the shipped world at
        # import time, which would silently reset a scratch world
        # loaded before it (slice 6 lesson).
        from fastapi.testclient import TestClient
        from backend.main import app, build_system_instruction
        self.build_system_instruction = build_system_instruction
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestRumors.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        with open(self.scratch) as f:
            world = json.load(f)
        world["locations"][0]["characters"].extend([JOREN, CORVIN])
        for loc in world["locations"]:
            for char in loc.get("characters", []):
                if char["name"] == "Brom":
                    # Brom talks to Elara; Elara talks to no one,
                    # so the chain ends with her.
                    char["connections"] = ["Elara"]
        with open(self.scratch, "w") as f:
            json.dump(world, f)
        initialize_game_world("TestRumors")
        self.gsm = GameStateManager()
        # A unique session per run, not a fixed id: the ledger
        # dedupes by entry id, so a re-run against a fixed session
        # would learn nothing the second time and go red -- the
        # repeat-run instability the older test files still carry.
        self.session_id = (
            f"test_session_rumors_turn_{self._testMethodName}_"
            f"{uuid.uuid4().hex[:8]}")
        self.gsm.create_session(self.session_id, "TestPlayer")
        self.client = TestClient(app)

    def tearDown(self):
        import sqlite3
        conn = sqlite3.connect('backend/game_data.db')
        conn.execute("DELETE FROM challenges WHERE session_id = ?",
                     (self.session_id,))
        conn.execute("DELETE FROM quests WHERE session_id = ?",
                     (self.session_id,))
        conn.commit()
        conn.close()
        if os.path.exists(self.scratch):
            os.remove(self.scratch)
        tmp = self.scratch + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        initialize_game_world("Elodia")

    def _interact(self, message, replies):
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(side_effect=replies)), \
             patch('backend.gemini_service.random.randint',
                   return_value=20):
            r = self.client.post("/interact", json={
                "session_id": self.session_id, "message": message})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _talk_to(self, name):
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": "leave"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": f"talk to {name}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.gsm.get_conversation_partner(self.session_id), name)

    def _seed_record(self):
        self.gsm.set_rumor_records(self.session_id, [{
            "subject": "Corvin", "text": TEXT, "said_by": "Joren",
            "turn": 2, "verdict": "false", "status": "open",
            "heard_by": [{"npc": "Joren", "hop": 0}],
            "confidence": "medium",
            "recorded_at": {"day": 1, "hour": 8},
        }])

    def test_rumor_walks_the_chain_one_hop_per_tick(self):
        self._seed_record()
        # Tick 1: Brom hears it, low confidence, from Joren.
        log = self.gsm.advance_clock(self.session_id)
        hops = [e for e in log if e.get("kind") == "rumor"]
        self.assertEqual(len(hops), 1)
        self.assertEqual((hops[0]["from"], hops[0]["to"]),
                         ("Joren", "Brom"))
        knowledge = self.gsm.get_npc_rumor_knowledge(self.session_id)
        self.assertEqual(list(knowledge), ["Brom"])
        entry = knowledge["Brom"][0]
        self.assertEqual(entry["fact"], TEXT)
        self.assertEqual(entry["confidence"], "low")
        self.assertEqual(entry["provenance"]["from_npc"], "Joren")
        self.assertEqual(entry["provenance"]["hop"], 1)
        # Tick 2: Elara hears it from Brom, still low (floored).
        log = self.gsm.advance_clock(self.session_id)
        hops = [e for e in log if e.get("kind") == "rumor"]
        self.assertEqual(len(hops), 1)
        self.assertEqual((hops[0]["from"], hops[0]["to"]),
                         ("Brom", "Elara"))
        knowledge = self.gsm.get_npc_rumor_knowledge(self.session_id)
        self.assertEqual(sorted(knowledge), ["Brom", "Elara"])
        self.assertEqual(
            knowledge["Elara"][0]["provenance"]["from_npc"], "Brom")
        self.assertEqual(knowledge["Elara"][0]["provenance"]["hop"], 2)
        # Tick 3: nowhere left to go -- Elara talks to no one.
        log = self.gsm.advance_clock(self.session_id)
        hops = [e for e in log if e.get("kind") == "rumor"]
        self.assertEqual(hops, [])
        # Corvin never hears it: unconnected, and he is the subject.
        self.assertNotIn("Corvin", knowledge)
        # The record is the ledger of who heard what.
        records = self.gsm.get_rumor_records(self.session_id)
        self.assertEqual(records[0]["heard_by"],
                         [{"npc": "Joren", "hop": 0},
                          {"npc": "Brom", "hop": 1},
                          {"npc": "Elara", "hop": 2}])

    def test_corrected_record_stops_spreading(self):
        self._seed_record()
        self.gsm.advance_clock(self.session_id)
        records = self.gsm.get_rumor_records(self.session_id)
        self.assertEqual(
            [h["npc"] for h in records[0]["heard_by"]], ["Joren", "Brom"])
        # The player learns the truth; the record settles.
        records[0]["status"] = "corrected"
        self.gsm.set_rumor_records(self.session_id, records)
        self.gsm.advance_clock(self.session_id)
        knowledge = self.gsm.get_npc_rumor_knowledge(self.session_id)
        self.assertNotIn("Elara", knowledge)

    def test_full_loop_tell_hear_share_learn(self):
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Joren"})
        self.assertEqual(talk.status_code, 200)
        joren_turn = json.dumps({
            "dialogue": "Corvin? Aye, he camps in the Whispering "
                        "Woods, or so I've heard.",
            "claims": [{"text": TEXT, "kind": "rumor",
                        "subject": "Corvin"}],
            "knowledge_shared": [],
        })
        body = self._interact("What do you know about Corvin?",
                              [joren_turn])
        metadata = body["metadata"]
        # Recorded false, and the same turn's tick already carried
        # it to Brom -- the record runs before the tick.
        self.assertEqual(metadata["rumors"][0]["verdict"], "false")
        hops = [e for e in metadata["tick"]
                if e.get("kind") == "rumor"]
        self.assertEqual(len(hops), 1)
        self.assertEqual((hops[0]["from"], hops[0]["to"]),
                         ("Joren", "Brom"))
        # Brom's prompt now carries the rumor, naming the teller.
        instruction = self.build_system_instruction(
            self.session_id, "Brom")
        self.assertIn(TEXT, instruction)
        self.assertIn("heard from Joren", instruction)
        self.assertIn("confidence: low", instruction)
        # Brom shares it; the player learns it once, at low
        # confidence, with no DC weight.
        rumor_id = self.gsm.get_npc_rumor_knowledge(
            self.session_id)["Brom"][0]["id"]
        self._talk_to("Brom")
        brom_turn = json.dumps({
            "dialogue": "Joren was saying Corvin camps in the "
                        "Whispering Woods. Heard it from him, mind.",
            "claims": [{"text": TEXT, "kind": "rumor",
                        "subject": "Corvin"}],
            "knowledge_shared": [rumor_id],
        })
        body = self._interact("What did Joren tell you?", [brom_turn])
        learned = body["metadata"]["knowledge_learned"]
        self.assertEqual(learned, [{"id": rumor_id, "topic": "Corvin"}])
        ledger = self.gsm.get_learned_knowledge(self.session_id)
        entry = next(e for e in ledger if e["id"] == rumor_id)
        self.assertEqual(entry["confidence"], "low")
        self.assertEqual(entry["dc_modifier"], 0)


if __name__ == "__main__":
    unittest.main()
