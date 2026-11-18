"""Slice 9 end-to-end: NPC agendas through the real endpoints.

A scratch world (TestAgenda.json, a copy of the seeded Elodia
with agendas injected), never the shipped file. Elara translates
the old codex in 16 hours -- a fact with provenance -- then leaves
for the Moonpetal Glade 8 hours later; Brom packs up and moves in
8 hours, with no schedule, so his move is visible in presence.

The world keeps moving while the player walks and talks: a fact
fires on the tick that completes its step, rides in the turn
metadata, lands in world_facts, and shows up in the bindings the
next turn -- so the model can report exactly what the engine
decided. A single 24-hour jump fires only the first step: one
firing per NPC per tick call, by design.
"""
import json
import os
import shutil
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
from backend.game_world import game_world, initialize_game_world


ELARA_FACT = "Elara has finished translating the old codex."


class TestAgendaTurn(unittest.TestCase):
    def setUp(self):
        # Import main first: it initializes the shipped world at
        # import time (line 103), which would silently reset a scratch
        # world loaded before it. Importing it here means the scratch
        # init below wins -- and the save path follows the loaded file
        # (slice 6 fix in game_world.py), not the world's name.
        from fastapi.testclient import TestClient
        from backend.main import app, build_system_instruction
        self.build_system_instruction = build_system_instruction
        self.worlds_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worlds'))
        self.scratch = os.path.join(self.worlds_dir, "TestAgenda.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        with open(self.scratch) as f:
            world = json.load(f)
        for loc in world["locations"]:
            for char in loc.get("characters", []):
                if char["name"] == "Elara":
                    char["agenda"] = [
                        {"id": "translate-codex", "duration_hours": 16,
                         "effect": {"fact": {"topic": "the old codex",
                                             "text": ELARA_FACT}}},
                        {"id": "leave-for-grove",
                         "after_step": "translate-codex",
                         "duration_hours": 8,
                         "effect": {"move": {"location": "Moonpetal Glade"}}},
                    ]
                if char["name"] == "Brom":
                    char["agenda"] = [
                        {"id": "pack-up", "duration_hours": 8,
                         "effect": {"move": {"location": "Moonpetal Glade"}}},
                    ]
        with open(self.scratch, "w") as f:
            json.dump(world, f, indent=4)
        initialize_game_world("TestAgenda")
        self.gsm = GameStateManager()
        # A fresh session per test, with any stale row from a repeat
        # run in the same clone removed first: create_session returns
        # early when the id already exists, so a leftover session
        # would hand the test yesterday's agenda progress.
        self.session_id = (
            f"test_session_agenda_turn_{self._testMethodName}")
        conn = sqlite3.connect('backend/game_data.db')
        conn.execute("DELETE FROM sessions WHERE session_id = ?",
                     (self.session_id,))
        conn.execute("DELETE FROM player_stats WHERE session_id = ?",
                     (self.session_id,))
        conn.commit()
        conn.close()
        self.gsm.create_session(self.session_id, "TestPlayer")
        self.client = TestClient(app)

    def tearDown(self):
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

    def _talk(self, npc_name):
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": f"talk to {npc_name}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.gsm.get_conversation_partner(self.session_id),
                         npc_name)

    def _interact(self, message, replies):
        with patch('backend.gemini_service.generate_llm_response',
                   new=AsyncMock(side_effect=replies)), \
             patch('backend.gemini_service.random.randint',
                   return_value=20):
            r = self.client.post("/interact", json={
                "session_id": self.session_id, "message": message})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _others_line(self):
        # The Architect is the partner, so everyone present in
        # Oakhaven shows up in "Others here" -- presence, not
        # tile distance, is what this asserts. Only that line is
        # returned: the world-facts line may name the same NPC
        # ("Elara has finished translating...") after they leave,
        # and a whole-instruction search would confuse the two.
        self._talk("The Architect")
        instruction = self.build_system_instruction(
            self.session_id, "The Architect")
        start = instruction.find("Others here:")
        if start == -1:
            return ""
        end = instruction.find(".", start)
        return instruction[start:end]

    def test_agenda_state_starts_empty(self):
        self.assertEqual(self.gsm.get_agenda_state(self.session_id), {})
        self.assertEqual(self.gsm.get_world_facts(self.session_id), [])

    def test_greeting_does_not_advance_agenda(self):
        self._talk("Elara")
        self.assertEqual(self.gsm.get_agenda_state(self.session_id), {})
        self.assertEqual(self.gsm.get_world_facts(self.session_id), [])

    def test_fact_fires_at_sixteen_hours(self):
        # 15 hours in: the translation is still in progress, and
        # nothing has happened in the world yet.
        self.gsm.advance_clock(self.session_id, 15)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 23})
        self.assertEqual(self.gsm.get_world_facts(self.session_id), [])
        # The 16th hour completes it: Day 2, 00:00.
        self.gsm.advance_clock(self.session_id, 1)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 2, "hour": 0})
        facts = self.gsm.get_world_facts(self.session_id)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["text"], ELARA_FACT)
        self.assertEqual(facts[0]["topic"], "the old codex")
        self.assertEqual(facts[0]["provenance"], {
            "source": "agenda", "npc": "Elara", "step": "translate-codex",
            "day": 2, "hour": 0})
        # The next turn's bindings carry the fact, so the model can
        # report exactly what the engine decided.
        instruction = self.build_system_instruction(self.session_id, "Brom")
        self.assertIn(ELARA_FACT, instruction)
        self.assertIn("Day 2, 00:00", instruction)

    def test_fact_rides_in_turn_metadata(self):
        # Drive 15 hours directly, then resolve one real turn: the
        # 16th hour lands in the turn's tick, and the agenda entry
        # rides in the metadata alongside the clock entry.
        self.gsm.advance_clock(self.session_id, 15)
        self._talk("Elara")
        body = self._interact("How is the translation going?",
                              [json.dumps({"dialogue": "Nearly done."})])
        self.assertEqual(body["metadata"]["tick"], [
            {"kind": "clock", "day": 2, "hour": 0, "time_of_day": "night"},
            {"kind": "agenda", "npc": "Elara", "step": "translate-codex",
             "effect": {"kind": "fact", "topic": "the old codex",
                        "text": ELARA_FACT},
             "day": 2, "hour": 0},
        ])
        self.assertEqual(len(self.gsm.get_world_facts(self.session_id)), 1)

    def test_move_changes_presence(self):
        # Brom is in Oakhaven at the start -- no schedule, always home.
        self.assertIn("Brom", self._others_line())
        self.gsm.advance_clock(self.session_id, 8)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 16})
        # He has packed up and moved: gone from Oakhaven.
        self.assertNotIn("Brom", self._others_line())
        state = self.gsm.get_agenda_state(self.session_id)
        self.assertEqual(state["Brom"]["done"], ["pack-up"])

    def test_sequence_fact_then_move(self):
        # Hour by hour, the way real ticks land: the fact at 16h,
        # the move 8h after that. Elara stays findable in town
        # until the move fires.
        for _ in range(16):
            self.gsm.advance_clock(self.session_id, 1)
        self.assertEqual(len(self.gsm.get_world_facts(self.session_id)), 1)
        self.assertIn("Elara", self._others_line())
        for _ in range(8):
            self.gsm.advance_clock(self.session_id, 1)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 2, "hour": 8})
        self.assertNotIn("Elara", self._others_line())
        state = self.gsm.get_agenda_state(self.session_id)
        self.assertEqual(state["Elara"]["done"],
                         ["translate-codex", "leave-for-grove"])
        # The move is not a fact: the world facts still hold only
        # the translation.
        self.assertEqual(len(self.gsm.get_world_facts(self.session_id)), 1)

    def test_single_large_jump_fires_one_step(self):
        # A 24-hour jump in one call finishes the 16-hour
        # translation but does not cascade into the 8-hour move:
        # one firing per NPC per tick call, by design. The move
        # fires on the next tick.
        self.gsm.advance_clock(self.session_id, 24)
        state = self.gsm.get_agenda_state(self.session_id)
        self.assertEqual(state["Elara"]["done"], ["translate-codex"])
        self.assertIn("Elara", self._others_line())
        self.gsm.advance_clock(self.session_id, 8)
        state = self.gsm.get_agenda_state(self.session_id)
        self.assertEqual(state["Elara"]["done"],
                         ["translate-codex", "leave-for-grove"])
        self.assertNotIn("Elara", self._others_line())


if __name__ == "__main__":
    unittest.main()
