import sys
import os
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager, VALUE_TIERS, NPC_RESOURCE_LEVELS
from backend.gemini_service import validate_llm_quest_rewards

class TestEconomy(unittest.TestCase):
    def setUp(self):
        self.gsm = GameStateManager()

    def test_value_tiers(self):
        self.assertIn("tier_1_minor", VALUE_TIERS)
        self.assertEqual(VALUE_TIERS["tier_1_minor"]["xp"], (25, 50))

    def test_npc_resource_levels(self):
        self.assertIn("poor", NPC_RESOURCE_LEVELS)
        self.assertIn("wealthy", NPC_RESOURCE_LEVELS)
        self.assertIn("substantial_gold", NPC_RESOURCE_LEVELS["wealthy"]["can_offer"])
        self.assertNotIn("gold", NPC_RESOURCE_LEVELS["destitute"]["can_offer"])

    def test_calculate_quest_rewards_destitute(self):
        npc_profile = {"name": "Beggar", "resource_level": "destitute"}
        rewards = self.gsm.calculate_quest_rewards("tier_1_minor", npc_profile, 50)
        
        self.assertIn("xp", rewards)
        self.assertTrue(25 <= rewards["xp"] <= 50)
        
        # Destitute NPC should not offer gold
        material_rewards = rewards["material_rewards"]
        gold_reward = next((r for r in material_rewards if r["type"] == "gold"), None)
        self.assertIsNone(gold_reward)

    def test_calculate_quest_rewards_wealthy(self):
        npc_profile = {"name": "Merchant", "resource_level": "wealthy"}
        rewards = self.gsm.calculate_quest_rewards("tier_2_standard", npc_profile, 50)
        
        self.assertIn("xp", rewards)
        self.assertTrue(100 <= rewards["xp"] <= 200)
        
        # Wealthy NPC should offer gold
        material_rewards = rewards["material_rewards"]
        gold_reward = next((r for r in material_rewards if r["type"] == "gold"), None)
        self.assertIsNotNone(gold_reward)
        # Tier 2 multiplier is 2.0. Wealthy range 200-1000.
        # min = 200 * 2.0 * 1.0 = 400
        # max = 1000 * 2.0 * 1.0 = 2000
        self.assertTrue(400 <= gold_reward["amount"] <= 2000)

    def test_validate_llm_quest_rewards_valid(self):
        npc_profile = {"name": "Merchant", "resource_level": "wealthy"}
        calculated_rewards = {
            "material_rewards": [{"type": "gold", "amount": 500}]
        }
        llm_output = {
            "quest_offered": {
                "rewards": {"gold": 500}
            }
        }
        errors = validate_llm_quest_rewards(llm_output, npc_profile, calculated_rewards)
        self.assertEqual(len(errors), 0)

    def test_validate_llm_quest_rewards_invalid_gold(self):
        npc_profile = {"name": "Beggar", "resource_level": "destitute"}
        calculated_rewards = {
            "material_rewards": []
        }
        llm_output = {
            "quest_offered": {
                "rewards": {"gold": 100}
            }
        }
        errors = validate_llm_quest_rewards(llm_output, npc_profile, calculated_rewards)
        self.assertTrue(len(errors) > 0)
        self.assertIn("cannot offer gold", errors[0])

    def test_validate_llm_quest_rewards_inflated_gold(self):
        npc_profile = {"name": "Merchant", "resource_level": "wealthy"}
        calculated_rewards = {
            "material_rewards": [{"type": "gold", "amount": 100}]
        }
        llm_output = {
            "quest_offered": {
                "rewards": {"gold": 500} # 5x calculated
            }
        }
        errors = validate_llm_quest_rewards(llm_output, npc_profile, calculated_rewards)
        self.assertTrue(len(errors) > 0)
        self.assertIn("Gold amount too high", errors[0])

if __name__ == '__main__':
    unittest.main()
