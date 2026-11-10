"""Slice 6 end-to-end: knowledge modifiers through the real /interact
endpoint, with a scripted provider.

The Corvin worked example (§14): a hard persuasion check sits at the
top of the elite band, and a fresh character auto-fails it. Elara's
briefing, once learned, is worth -5; Joren's knowledge another -5.
All turns run against a scratch world (TestWriteback.json, a copy of
the seeded Elodia), never the shipped file. The DC rolls are pinned
by patching randint to the band maximum, so the expected DCs are
exact: 20 unmodified, 15 with the briefing.
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


class TestKnowledgeTurn(unittest.TestCase):
    def setUp(self):
        # Import main first: it initializes the shipped world at
        # import time (line 103), which would silently reset a scratch
        # world loaded before it. Importing it here means the scratch
        # init below wins — and the save path follows the loaded file
        # (slice 6 fix in game_world.py), not the world's name.
        from fastapi.testclient import TestClient
        from backend.main import app
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestWriteback.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        initialize_game_world("TestWriteback")
        self.gsm = GameStateManager()
        # A fresh session per test: the ledger and referrals are
        # session state, and an earlier test's learning must not leak
        # into a later test's control case.
        self.session_id = (
            f"test_session_knowledge_turn_{self._testMethodName}")
        self.gsm.create_session(self.session_id, "TestPlayer")
        self.client = TestClient(app)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Elara"})
        self.assertEqual(talk.status_code, 200)
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id),
                         "Elara")

    def tearDown(self):
        # The quests/challenges tables are shared across sessions, and
        # the turn-schema e2e queries them without a session filter —
        # so this session's moonpetal_poacher offer would leak a third
        # challenge into its count. Remove this session's rows.
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

    def _persuasion_check(self):
        return json.dumps({
            "dialogue": "Corvin must be made to see reason.",
            "skill_check": {
                "type": "charisma", "difficulty": "hard",
                "subject": "Corvin",
                "description": "Persuade Corvin to leave the moonpetal alone.",
                "success_response": "He listens.",
                "failure_response": "He turns away.",
            },
        })

    def test_briefing_lowers_the_dc(self):
        share = json.dumps({
            "dialogue": "Without moonpetal there are no fever remedies, "
                        "and the town has no other source.",
            "knowledge_shared": ["elara-k5"],
        })
        body = self._interact("What does the taking cost the town?",
                              [share])
        self.assertEqual(body["metadata"]["knowledge_learned"],
                         [{"id": "elara-k5", "topic": "Corvin"}])
        self.assertEqual(
            [e["id"] for e in
             self.gsm.get_learned_knowledge(self.session_id)],
            ["elara-k5"])

        body = self._interact("I will talk to Corvin.",
                              [self._persuasion_check()])
        sc = body["metadata"]["skill_check"]
        self.assertEqual(sc["dc"], 15)
        self.assertEqual(sc["knowledge_modifiers"], [-5])

    def test_no_knowledge_no_modifier(self):
        body = self._interact("I will talk to Corvin.",
                              [self._persuasion_check()])
        sc = body["metadata"]["skill_check"]
        self.assertEqual(sc["dc"], 20)
        self.assertNotIn("knowledge_modifiers", sc)

    def test_unknown_shared_id_dropped(self):
        turn = json.dumps({
            "dialogue": "I know a secret.",
            "knowledge_shared": ["nope-k9"],
        })
        # Finding 7: pin both halves of the "dropped and logged" path.
        with self.assertLogs("backend.main", level="WARNING") as cm:
            body = self._interact("Tell me something.", [turn])
        self.assertNotIn("knowledge_learned", body["metadata"])
        self.assertEqual(
            self.gsm.get_learned_knowledge(self.session_id), [])
        self.assertTrue(any("nope-k9" in line for line in cm.output))

    def test_quest_challenge_modifier(self):
        # Learn the briefing first.
        share = json.dumps({
            "dialogue": "Without moonpetal there are no fever remedies.",
            "knowledge_shared": ["elara-k5"],
        })
        self._interact("What does the taking cost?", [share])

        # The recorded Turn 1 offer, kept as an in-repo fixture (Ellie
        # review, finding 1): the e2e used to read it from outside the
        # repo, which went red on a fresh clone. The fixture's rewards
        # are already stripped of items (Elara cannot offer them).
        with open(os.path.join(
                os.path.dirname(__file__), 'fixtures',
                'turn1_quest_offer.json')) as f:
            recorded = json.load(f)
        offer = json.dumps({
            "dialogue": recorded["dialogue"],
            "quest_offered": recorded["quest_offered"],
            "new_entities": recorded["new_entities"],
        })
        body = self._interact("What do you need?", [offer])
        # The challenge DCs live in the response metadata: the
        # challenges table has no column for the modifiers, and
        # get_quest_log only lists active/completed quest descriptions.
        offered = body["metadata"]["quest_offered"]
        challenges = {c["id"]: c for c in offered["challenges"]}
        # The persuasion challenge is about Corvin, charisma: -5.
        self.assertEqual(challenges["persuade_corvin"]["dc"], 15)
        self.assertEqual(
            challenges["persuade_corvin"]["knowledge_modifiers"], [-5])
        # The tracking challenge is dexterity: the briefing does not
        # apply, even though it is about the same quest.
        self.assertEqual(challenges["track_corvin"]["dc"], 20)
        self.assertNotIn("knowledge_modifiers",
                         challenges["track_corvin"])
        # The point of the worked example: with the briefing, the
        # persuasion is rollable; without it, the margin rule would
        # auto-fail it.
        self.assertTrue(challenges["persuade_corvin"]["requires_roll"])
        self.assertFalse(challenges["track_corvin"]["requires_roll"])
        # Corvin committed through the write-back, with the knowledge
        # the turn proposed for him — none here, so his set is empty.
        corvin = game_world.get_character("Corvin", "Oakhaven")
        self.assertIsNotNone(corvin)
        self.assertEqual(corvin.knowledge, [])

    def test_knowledge_stacks_across_referral(self):
        # Finding 2: the cumulative leg of the worked example, end to
        # end — learn from Elara, then from Joren, then re-check. The
        # replay covers it; this pins it in the suite.
        share = json.dumps({
            "dialogue": "Without moonpetal there are no fever remedies.",
            "knowledge_shared": ["elara-k5"],
        })
        self._interact("What does the taking cost?", [share])

        joren = json.dumps({
            "dialogue": "Ask Joren the trapper — he has seen the man.",
            "referrals": [{"to_name": "Joren", "to_kind": "person",
                           "reason": "He has seen the man."}],
            "new_entities": [{
                "kind": "character", "name": "Joren",
                "description": "A trapper who works the deep woods.",
                "location": "Oakhaven",
                "knowledge": [{
                    "id": "joren-k1", "topic": "Corvin",
                    "fact": "He takes the roots for his sick wife's medicine.",
                    "source": "saw", "confidence": "high",
                    "dc_modifier": -5, "applies_to": "charisma",
                }],
            }],
        })
        self._interact("Who would know more?", [joren])
        self.assertEqual(
            [e.id for e in
             game_world.get_character("Joren", "Oakhaven").knowledge],
            ["joren-k1"])

        leave = self.client.post("/command", json={
            "session_id": self.session_id, "command": "leave"})
        self.assertEqual(leave.status_code, 200)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Joren"})
        self.assertEqual(talk.status_code, 200)
        self.assertTrue(
            self.gsm.get_pending_referrals(self.session_id)[0]["followed"])

        joren_share = json.dumps({
            "dialogue": "He takes the roots for his sick wife. Coin for medicine.",
            "knowledge_shared": ["joren-k1"],
        })
        self._interact("What do you know of Corvin?", [joren_share])
        self.assertEqual(
            [e["id"] for e in
             self.gsm.get_learned_knowledge(self.session_id)],
            ["elara-k5", "joren-k1"])

        body = self._interact("I will talk to Corvin.",
                              [self._persuasion_check()])
        sc = body["metadata"]["skill_check"]
        self.assertEqual(sc["dc"], 10)
        self.assertEqual(sc["knowledge_modifiers"], [-5, -5])

    def test_referral_recorded_and_followed(self):
        refer = json.dumps({
            "dialogue": "Ask Brom — he knows every hinge in town.",
            "referrals": [{"to_name": "Brom", "to_kind": "person",
                           "reason": "He knows the town's ironwork."}],
        })
        self._interact("Who would know about the mill?", [refer])
        pending = self.gsm.get_pending_referrals(self.session_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["to_name"], "Brom")
        self.assertFalse(pending[0]["followed"])
        self.assertEqual(pending[0]["from_npc"], "Elara")

        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "leave"})
        self.assertEqual(talk.status_code, 200)
        talk = self.client.post("/command", json={
            "session_id": self.session_id, "command": "talk to Brom"})
        self.assertEqual(talk.status_code, 200)
        pending = self.gsm.get_pending_referrals(self.session_id)
        self.assertTrue(pending[0]["followed"])

        from backend.main import build_system_instruction
        instruction = build_system_instruction(self.session_id, "Brom")
        self.assertIn("sent to you", instruction)
        self.assertIn("Elara", instruction)


if __name__ == "__main__":
    unittest.main()
