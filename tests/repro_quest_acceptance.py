import sys
import os
import unittest
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world

class TestQuestAcceptance(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_repro"
        # Ensure clean state
        if self.gsm.session_exists(self.session_id):
            # We don't have a delete_session, but we can overwrite
            pass
        self.gsm.create_session(self.session_id, "TestPlayer")

    def test_quest_acceptance_flow(self):
        # 1. Offer a quest
        quest_data = {
            "id": "test_quest_001",
            "giver_npc": "TestNPC",
            "description": "A test quest.",
            "status": "offered",
            "challenges": []
        }
        self.gsm.add_quest(self.session_id, quest_data)
        
        # Verify it's NOT in active quests yet
        active_quests = self.gsm.get_active_quests(self.session_id)
        active_ids = [q['id'] for q in active_quests]
        self.assertNotIn("test_quest_001", active_ids)
        
        # 2. Accept the quest
        self.gsm.accept_quest(self.session_id, "test_quest_001")
        
        # 3. Verify it IS in active quests
        active_quests = self.gsm.get_active_quests(self.session_id)
        active_ids = [q['id'] for q in active_quests]
        self.assertIn("test_quest_001", active_ids)
        
        # 4. Verify status is active
        quest = next(q for q in active_quests if q['id'] == "test_quest_001")
        self.assertEqual(quest['status'], 'active')

if __name__ == '__main__':
    unittest.main()
