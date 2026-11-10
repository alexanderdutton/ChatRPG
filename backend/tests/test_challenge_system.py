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
        # Valid Quest, model format (slice 1): tier, lowercase type, no dc.
        # The engine assigns the DC; a quest written this way is correct.
        valid_quest = {
            "id": "q1",
            "description": "Test Quest",
            "accept_response": "Great.",
            "refuse_response": "A pity.",
            "tier": "average",
            "challenges": [
                {
                    "id": "c1",
                    "type": "strength",
                    "tier": "average",
                    "description": "Lift rock"
                }
            ]
        }
        self.assertEqual(validate_quest_output(valid_quest), [])

        # Also valid: difficulty label instead of tier, and a dc supplied
        # by an older prompt is tolerated (the engine overwrites it).
        valid_label_quest = {
            "id": "q1b",
            "description": "Test Quest",
            "accept_response": "Great.",
            "refuse_response": "A pity.",
            "challenges": [
                {
                    "id": "c1b",
                    "type": "strength",
                    "difficulty": "medium",
                    "dc": 10,
                    "description": "Lift rock"
                }
            ]
        }
        self.assertEqual(validate_quest_output(valid_label_quest), [])

        # Invalid Type
        invalid_type = {
            "id": "q3",
            "description": "Test Quest",
            "accept_response": "Great.",
            "refuse_response": "A pity.",
            "challenges": [
                {
                    "id": "c3",
                    "type": "magic", # Invalid
                    "difficulty": "medium",
                    "description": "Cast spell"
                }
            ]
        }
        self.assertTrue(len(validate_quest_output(invalid_type)) > 0)

        # Tier and difficulty label disagree (average band is 10-12,
        # heroic is the boss band, 22-25)
        mismatched = {
            "id": "q4",
            "description": "Test Quest",
            "accept_response": "Great.",
            "refuse_response": "A pity.",
            "challenges": [
                {
                    "id": "c4",
                    "type": "strength",
                    "tier": "average",
                    "difficulty": "heroic",
                    "description": "Lift rock"
                }
            ]
        }
        self.assertTrue(len(validate_quest_output(mismatched)) > 0)

        # Unknown tier
        unknown_tier = {
            "id": "q5",
            "description": "Test Quest",
            "accept_response": "Great.",
            "refuse_response": "A pity.",
            "tier": "mythic",
            "challenges": [
                {
                    "id": "c5",
                    "type": "strength",
                    "tier": "mythic",
                    "description": "Lift rock"
                }
            ]
        }
        self.assertTrue(len(validate_quest_output(unknown_tier)) > 0)

    def test_resolve_challenge(self):
        # Setup Quest and Challenge. Stats are 10, so the modifier bonus
        # is (10 - 10) // 2 = 0; totals below are roll + 0.
        quest_data = {
            "id": "test_quest_1",
            "description": "Test Quest",
            "giver_npc": "TestGiver",
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
            # Test Success (Roll 10 + modifier 0 = 10 >= DC 10)
            mock_rand.return_value = 10
            result = self.gsm.resolve_challenge(self.session_id, "test_chal_1")
            self.assertTrue(result['success'])
            self.assertEqual(result['total'], 10)

            # A rolled failure: DC 15, roll 5 + modifier 0 = 5 < 15.
            quest_data_2 = {
                "id": "test_quest_2",
                "description": "Test Quest 2",
                "giver_npc": "TestGiver",
                "challenges": [
                    {
                        "id": "test_chal_2",
                        "type": "strength",
                        "difficulty": "medium",
                        "dc": 15,
                        "description": "Medium Test"
                    }
                ]
            }
            self.gsm.add_quest(self.session_id, quest_data_2)

            mock_rand.return_value = 5
            result = self.gsm.resolve_challenge(self.session_id, "test_chal_2")
            self.assertFalse(result['success'])
            self.assertEqual(result['total'], 5)
            self.assertFalse(result.get('auto_resolved', False))

            # An auto-failure: DC 25 is 15 below a stat of 10, past the
            # -10 margin, so no roll happens at all.
            quest_data_3 = {
                "id": "test_quest_3",
                "description": "Test Quest 3",
                "giver_npc": "TestGiver",
                "challenges": [
                    {
                        "id": "test_chal_3",
                        "type": "strength",
                        "difficulty": "heroic",
                        "dc": 25,
                        "description": "Hard Test"
                    }
                ]
            }
            self.gsm.add_quest(self.session_id, quest_data_3)

            result = self.gsm.resolve_challenge(self.session_id, "test_chal_3")
            self.assertFalse(result['success'])
            self.assertTrue(result.get('auto_resolved', False))

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

    def test_stub_shared_across_sessions(self):
        # Combined-path test (PR #6 + #7 integration, 2026-11-09):
        # entity_stubs.id is the PRIMARY KEY, global — not
        # per-session. Two sessions offering quests that mention the
        # same new entity must share the one stub row, not collide
        # on the second INSERT. Before the fix this raised
        # IntegrityError (UNIQUE constraint failed: entity_stubs.id)
        # and the unclosed connection cascaded into
        # "database is locked" for the rest of the run.
        quest_a = {
            "id": "test_quest_shared_a",
            "description": "Shared Entity Quest A",
            "involved_entities": ["shared_entity"],
        }
        quest_b = {
            "id": "test_quest_shared_b",
            "description": "Shared Entity Quest B",
            "involved_entities": ["shared_entity"],
        }
        self.gsm.add_quest(self.session_id, quest_a)

        other_session = "test_session_challenge_other"
        self.gsm.create_session(other_session, "TestPlayer")
        self.gsm.add_quest(other_session, quest_b)

        import sqlite3
        conn = sqlite3.connect('backend/game_data.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM entity_stubs WHERE id = ?",
                       ("shared_entity",))
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

if __name__ == '__main__':
    unittest.main()
