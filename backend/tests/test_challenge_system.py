import unittest
import json
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Set dummy API key for testing
os.environ["GEMINI_API_KEY"] = "test_key"

# Mock google.genai to prevent connection attempts
sys.modules["google"] = MagicMock()
sys.modules["google.genai"] = MagicMock()
sys.modules["google.genai"].__file__ = "mocked_genai_file"
sys.modules["google.genai.types"] = MagicMock()
sys.modules["google.genai.types"].__file__ = "mocked_genai_types_file"

from backend.game_state_manager import GameStateManager
from backend.gemini_service import validate_quest_output
from backend.game_world import initialize_game_world

class TestChallengeSystem(unittest.TestCase):
    def setUp(self):
        # Initialize game world to ensure locations exist
        initialize_game_world("Elodia")
        
        self.gsm = GameStateManager()
        # Use a test session ID
        self.session_id = "test_session_challenge"
        self.gsm.create_session(self.session_id, "TestPlayer")

    def test_validate_quest_output(self):
        # Valid Quest
        valid_quest = {
            "id": "q1",
            "description": "Test Quest",
            "challenges": [
                {
                    "id": "c1",
                    "type": "strength",
                    "difficulty": "medium",
                    "dc": 15,
                    "description": "Lift rock"
                }
            ]
        }
        self.assertEqual(validate_quest_output(valid_quest), [])

        # Invalid DC
        invalid_dc = {
            "id": "q2",
            "description": "Test Quest",
            "challenges": [
                {
                    "id": "c2",
                    "type": "strength",
                    "difficulty": "medium",
                    "dc": 10, # Should be 15
                    "description": "Lift rock"
                }
            ]
        }
        self.assertTrue(len(validate_quest_output(invalid_dc)) > 0)

        # Invalid Type
        invalid_type = {
            "id": "q3",
            "description": "Test Quest",
            "challenges": [
                {
                    "id": "c3",
                    "type": "magic", # Invalid
                    "difficulty": "medium",
                    "dc": 15,
                    "description": "Cast spell"
                }
            ]
        }
        self.assertTrue(len(validate_quest_output(invalid_type)) > 0)

    def test_resolve_challenge(self):
        # Setup Quest and Challenge
        quest_data = {
            "id": "test_quest_1",
            "description": "Test Quest",
            "challenges": [
                {
                    "id": "test_chal_1",
                    "type": "strength",
                    "difficulty": "easy",
                    "dc": 10,
                    "description": "Easy Test"
                }
            ]
        }
        self.gsm.add_quest(self.session_id, quest_data)

        # Mock Random to ensure predictable roll
        with patch('random.randint') as mock_rand:
            # Test Success (Roll 10 + Stat 10 = 20 >= 10)
            mock_rand.return_value = 10
            result = self.gsm.resolve_challenge(self.session_id, "test_chal_1")
            self.assertTrue(result['success'])
            self.assertEqual(result['total'], 20)
            
            # Reset challenge for failure test (manually update DB or re-add)
            # Since we update 'completed', we should probably add a new challenge
            
            quest_data_2 = {
                "id": "test_quest_2",
                "description": "Test Quest 2",
                "challenges": [
                    {
                        "id": "test_chal_2",
                        "type": "strength",
                        "difficulty": "heroic",
                        "dc": 25,
                        "description": "Hard Test"
                    }
                ]
            }
            self.gsm.add_quest(self.session_id, quest_data_2)
            
            # Test Failure (Roll 5 + Stat 10 = 15 < 25)
            mock_rand.return_value = 5
            result = self.gsm.resolve_challenge(self.session_id, "test_chal_2")
            self.assertFalse(result['success'])

    def test_entity_stub_creation(self):
        # Add quest with new entities
        quest_data = {
            "id": "test_quest_entities",
            "description": "Entities Quest",
            "involved_entities": ["new_entity_1", "new_entity_2"]
        }
        self.gsm.add_quest(self.session_id, quest_data)
        
        # Check DB for stubs
        conn = self.gsm._get_session_data(self.session_id) # Just to get connection logic or use direct DB access
        # Direct DB access for verification
        import sqlite3
        conn = sqlite3.connect(self.gsm.DB_PATH if hasattr(self.gsm, 'DB_PATH') else 'backend/game_data.db') 
        # Wait, DB_PATH is module level in game_state_manager. accessing via instance might be tricky if not exposed.
        # It is exposed as global in module, but not on instance.
        # But init_db uses it.
        # Let's trust the method ran without error, or query via a helper if available.
        # We don't have a get_entity_stub method.
        # Let's just assume if no error it worked, or add a check if we can.
        # Actually, I can use `cursor` on the file path directly.
        
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM entity_stubs WHERE id = ?", ("new_entity_1",))
        stub = cursor.fetchone()
        self.assertIsNotNone(stub)
        self.assertEqual(stub[2], "New Entity 1") # Name title-cased
        conn.close()

if __name__ == '__main__':
    unittest.main()
