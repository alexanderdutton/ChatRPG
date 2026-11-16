"""Slice 8 end-to-end: the clock through the real endpoints.

A fresh session starts Day 1, 08:00. A resolved /interact turn
costs an hour and says so in the metadata; the greeting does not
tick. A successful move costs an hour; a wall does not. And a
schedule gates presence: Elara is in Oakhaven in the morning and at
the Moonpetal Glade in the afternoon, so the Architect's "Others
here" -- and the NPC list -- lose her once the clock passes noon.

All turns run against a scratch world (TestClock.json, a copy of the
seeded Elodia with Elara's schedule injected), never the shipped
file. The conversation partner is exempt from the schedule check:
the player walked up to them, so they are present by definition.
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


class TestClockTurn(unittest.TestCase):
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
        self.scratch = os.path.join(self.worlds_dir, "TestClock.json")
        shutil.copy(os.path.join(self.worlds_dir, "Elodia.json"),
                    self.scratch)
        # Elara's schedule, in the scratch copy only: mornings in
        # town, afternoons at the glade. The glade is a stub location
        # name, not a real location -- the point is she is not in
        # Oakhaven then, and the engine does not validate schedule
        # locations against the world.
        with open(self.scratch) as f:
            world = json.load(f)
        for loc in world["locations"]:
            for char in loc.get("characters", []):
                if char["name"] == "Elara":
                    char["schedule"] = [
                        {"start": 8, "end": 12, "location": "Oakhaven"},
                        {"start": 12, "end": 20, "location": "Moonpetal Glade"},
                    ]
        with open(self.scratch, "w") as f:
            json.dump(world, f, indent=4)
        initialize_game_world("TestClock")
        self.gsm = GameStateManager()
        # A fresh session per test, with any stale row from a repeat
        # run in the same clone removed first: create_session returns
        # early when the id already exists, so a leftover session
        # would hand the test yesterday's clock and history.
        self.session_id = (
            f"test_session_clock_turn_{self._testMethodName}")
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

    def test_clock_starts_day_one_morning(self):
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 8})
        self._talk("Elara")
        instruction = self.build_system_instruction(self.session_id, "Elara")
        self.assertIn("It is Day 1, 08:00 (morning).", instruction)

    def test_greeting_does_not_tick(self):
        # The greeting is delivered by the /command itself ("talk to
        # Elara" greets immediately in handle_command), before any
        # turn resolves -- so the clock stands still after it.
        self._talk("Elara")
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 8})

    def test_resolved_turn_ticks_one_hour(self):
        # The greeting already happened in /command, so this
        # /interact is a resolved turn -- and it costs an hour.
        self._talk("Elara")
        body = self._interact("How are the herbs?",
                              [json.dumps({"dialogue": "Drying well today."})])
        self.assertEqual(body["metadata"]["tick"],
                         [{"kind": "clock", "day": 1, "hour": 9,
                           "time_of_day": "morning"}])
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 9})
        instruction = self.build_system_instruction(self.session_id, "Elara")
        self.assertIn("It is Day 1, 09:00 (morning).", instruction)

    def test_move_ticks_and_wall_does_not(self):
        # Player starts at (3, 2). West to (2, 2) is open ground, west
        # again to (1, 2) is the market square; west a third time to
        # (0, 2) is a wall.
        for _ in range(2):
            r = self.client.post("/command", json={
                "session_id": self.session_id, "command": "go west"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 10})
        r = self.client.post("/command", json={
            "session_id": self.session_id, "command": "go west"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("wall", r.json()["dialogue"].lower())
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 10})

    def test_schedule_gates_presence(self):
        # The Architect is the partner here, so Elara shows up in
        # "Others here" -- at 08:00 she is in town.
        self._talk("The Architect")
        morning = self.build_system_instruction(
            self.session_id, "The Architect")
        self.assertIn("Elara", morning)
        self.assertIn("Others here:", morning)
        # Past noon she is at the glade: gone from the bindings,
        # while Brom (no schedule, always home) stays.
        self.gsm.advance_clock(self.session_id, 5)
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 13})
        afternoon = self.build_system_instruction(
            self.session_id, "The Architect")
        self.assertNotIn("Elara", afternoon)
        self.assertIn("Brom", afternoon)

    def test_schedule_gates_npc_list(self):
        # Walk to Elara's tile: west twice from (3, 2) to (1, 2).
        # Each successful move ticks an hour, so the clock reads
        # 10:00 when the player arrives -- still inside her morning
        # window, and she is listed. Three more hours and she is not.
        self.client.post("/command", json={
            "session_id": self.session_id, "command": "go west"})
        self.client.post("/command", json={
            "session_id": self.session_id, "command": "go west"})
        self.assertEqual(self.gsm.get_clock(self.session_id),
                         {"day": 1, "hour": 10})
        here = [n["name"] for n in
                self.gsm.get_npcs_in_location(self.session_id)]
        self.assertIn("Elara", here)
        self.gsm.advance_clock(self.session_id, 3)
        here = [n["name"] for n in
                self.gsm.get_npcs_in_location(self.session_id)]
        self.assertNotIn("Elara", here)


if __name__ == "__main__":
    unittest.main()
