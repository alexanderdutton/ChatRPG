"""Slice 7 end-to-end: gossip with reliability through the real
/interact endpoint, with a scripted provider.

The Corvin worked example (§14/§24), extended: Joren's knowledge
about *why* Corvin takes the roots is a rumor — heard secondhand,
medium confidence, -5 — and Joren also says, in dialogue, that
Corvin camps in the Whispering Woods, a rumor claim the engine can
check (the world has Corvin in Oakhaven). The line stands, the
record says false, no correction note. Later the player meets
Corvin and learns, saw/high, that he takes the roots for his sick
wife's medicine: the rumor flips to corrected, and the next Corvin
check draws the full modifier from the solid fact and only half
from the rumor.

All turns run against a scratch world (TestGossip.json, a copy of
the seeded Elodia with Joren and Corvin injected into Oakhaven),
never the shipped file. The DC rolls are pinned by patching randint
to the band maximum, so the expected DCs are exact: 20 unmodified,
13 with the rumor (-2) and the truth (-5) together.
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


JOREN = {
    "name": "Joren", "race": "Human", "occupation": "Trapper",
    "description": "A weathered trapper who knows the woods south of town.",
    "personality_prompt": "Joren is blunt and wary, and repeats what he hears.",
    "x": 2, "y": 2, "short_description": "A weathered trapper.",
    "knowledge": [
        {"id": "joren-k1", "topic": "Corvin",
         "fact": "He takes the roots for his sick wife's medicine.",
         "source": "heard_from", "confidence": "medium",
         "dc_modifier": -5, "applies_to": "charisma"},
        {"id": "joren-k2", "topic": "Corvin",
         "fact": "Everyone says he is dangerous.",
         "source": "everyone_says", "confidence": "low",
         "dc_modifier": -5, "applies_to": "charisma"},
    ],
}

CORVIN = {
    "name": "Corvin", "race": "Human", "occupation": "Poacher",
    "description": "A proud, defensive man who takes moonpetal roots.",
    "personality_prompt": "Corvin is proud and defensive with strangers.",
    "x": 3, "y": 2, "short_description": "A proud poacher.",
    "knowledge": [
        {"id": "corvin-k1", "topic": "Corvin",
         "fact": "I take the roots for my sick wife's medicine.",
         "source": "saw", "confidence": "high",
         "dc_modifier": -5, "applies_to": "charisma"},
    ],
}


class TestGossipTurn(unittest.TestCase):
    def setUp(self):
        # Import main first: it initializes the shipped world at
        # import time, which would silently reset a scratch world
        # loaded before it (slice 6 lesson).
        from fastapi.testclient import TestClient
        from backend.main import app
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestGossip.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        # Inject Joren and Corvin into the scratch copy only — the
        # shipped world gains no new NPC in this slice.
        with open(self.scratch) as f:
            world = json.load(f)
        world["locations"][0]["characters"].extend([JOREN, CORVIN])
        with open(self.scratch, "w") as f:
            json.dump(world, f)
        initialize_game_world("TestGossip")
        self.gsm = GameStateManager()
        # A unique session per run, not a fixed id: the ledger dedupes
        # by entry id, so a re-run against a fixed session would learn
        # nothing the second time and go red — the repeat-run
        # instability the older test files still carry.
        self.session_id = (
            f"test_session_gossip_turn_{self._testMethodName}_"
            f"{uuid.uuid4().hex[:8]}")
        self.gsm.create_session(self.session_id, "TestPlayer")
        self.client = TestClient(app)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Joren"})
        self.assertEqual(talk.status_code, 200)
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id),
                         "Joren")

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
        # In interaction mode every command but "leave" is blocked,
        # so step out of the current conversation first.
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": "leave"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": f"talk to {name}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.gsm.get_conversation_partner(self.session_id), name)

    def test_false_rumor_stands_but_is_recorded(self):
        joren_turn = json.dumps({
            "dialogue": "Corvin? Aye, he camps in the Whispering "
                        "Woods, or so I've heard. They say he takes "
                        "the roots for his sick wife's medicine — and "
                        "everyone says he's dangerous.",
            "claims": [{"text": "Corvin camps in the Whispering Woods.",
                        "kind": "rumor", "subject": "Corvin"}],
            "knowledge_shared": ["joren-k1", "joren-k2"],
        })
        body = self._interact("What do you know about Corvin?",
                              [joren_turn])
        metadata = body["metadata"]
        # Both entries learned — even the low-confidence one; the
        # player heard it, they just cannot rely on it.
        self.assertEqual(metadata["knowledge_learned"], [
            {"id": "joren-k1", "topic": "Corvin"},
            {"id": "joren-k2", "topic": "Corvin"}])
        # The false rumor stands in the dialogue, is recorded false,
        # and produces no correction note — diegetic fallibility.
        # Turn 2, not 1: the "talk to Joren" greeting already put one
        # model message in the history, and the turn number counts them.
        self.assertIn("camps in the Whispering Woods",
                      body["dialogue"])
        self.assertEqual(metadata["rumors"], [{
            "subject": "Corvin",
            "text": "Corvin camps in the Whispering Woods.",
            "said_by": "Joren", "turn": 2,
            "verdict": "false", "status": "open",
            # Slice 10: the spread fields, stamped from the session
            # clock -- still Day 1, 08:00, the greeting does not tick.
            "heard_by": [{"npc": "Joren", "hop": 0}],
            "confidence": "medium",
            "recorded_at": {"day": 1, "hour": 8}}])
        claims = metadata["claims_validation"]["claims"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["verdict"], "contradicts")
        self.assertEqual(claims[0]["action"], "allow_wrong")
        self.assertEqual(
            metadata["claims_validation"]["correction_notes"], [])
        self.assertEqual(
            [r["status"] for r in
             self.gsm.get_rumor_records(self.session_id)],
            ["open"])

    def test_truth_corrects_the_rumor_and_scales_the_dice(self):
        joren_turn = json.dumps({
            "dialogue": "Corvin? Aye, he camps in the Whispering "
                        "Woods, or so I've heard. They say he takes "
                        "the roots for his sick wife's medicine.",
            "claims": [{"text": "Corvin camps in the Whispering Woods.",
                        "kind": "rumor", "subject": "Corvin"}],
            "knowledge_shared": ["joren-k1", "joren-k2"],
        })
        self._interact("What do you know about Corvin?", [joren_turn])

        self._talk_to("Corvin")
        corvin_turn = json.dumps({
            "dialogue": "I take the roots for my sick wife's "
                        "medicine. That's the truth of it.",
            "knowledge_shared": ["corvin-k1"],
        })
        body = self._interact("Why do you take the roots?",
                              [corvin_turn])
        # The saw/high learn corrects the open false rumor.
        self.assertEqual(body["metadata"]["rumor_updates"], [{
            "subject": "Corvin",
            "text": "Corvin camps in the Whispering Woods.",
            "said_by": "Joren",
            "old_status": "open", "new_status": "corrected",
            "settled_by": "corvin-k1"}])
        self.assertEqual(
            [r["status"] for r in
             self.gsm.get_rumor_records(self.session_id)],
            ["corrected"])

        # The persuasion check now draws the full -5 from the solid
        # fact, only -2 from the medium rumor, and nothing from the
        # low-confidence gossip: 20 - 7 = 13.
        check = json.dumps({
            "dialogue": "Corvin must be made to see reason.",
            "skill_check": {
                "type": "charisma", "difficulty": "hard",
                "subject": "Corvin",
                "description": "Persuade Corvin to leave the moonpetal alone.",
                "success_response": "He listens.",
                "failure_response": "He turns away.",
            },
        })
        body = self._interact("I will talk to Corvin.", [check])
        sc = body["metadata"]["skill_check"]
        self.assertEqual(sc["dc"], 13)
        self.assertEqual(sc["knowledge_modifiers"], [-2, -5])
        self.assertEqual(
            [(b["id"], b["listed_modifier"], b["applied_modifier"])
             for b in sc["knowledge_breakdown"]],
            [("joren-k1", -5, -2), ("joren-k2", -5, 0),
             ("corvin-k1", -5, -5)])


if __name__ == "__main__":
    unittest.main()
