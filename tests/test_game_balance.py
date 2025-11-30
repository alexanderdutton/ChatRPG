import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager, DEFAULT_NPC_STATE, RELATIONSHIP_MODIFIERS
from backend.game_world import initialize_game_world

class TestGameBalance(unittest.TestCase):
    
    def setUp(self):
        # Initialize Game World first
        initialize_game_world("Elodia")
        
        self.gsm = GameStateManager()
        self.session_id = "test_session_balance"
        
        # Clean up previous run if any
        import sqlite3
        conn = sqlite3.connect(self.gsm.DB_PATH if hasattr(self.gsm, 'DB_PATH') else os.path.join(os.path.dirname(__file__), '..', 'backend', 'game_data.db'))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE session_id = ?", (self.session_id,))
        cursor.execute("DELETE FROM player_stats WHERE session_id = ?", (self.session_id,))
        cursor.execute("DELETE FROM quests WHERE session_id = ?", (self.session_id,))
        cursor.execute("DELETE FROM challenges WHERE session_id = ?", (self.session_id,))
        conn.commit()
        conn.close()

        self.gsm.create_session(self.session_id, "TestPlayer")
        
        # Setup basic stats (Direct DB update since player_stats is a table)
        conn = sqlite3.connect(self.gsm.DB_PATH if hasattr(self.gsm, 'DB_PATH') else os.path.join(os.path.dirname(__file__), '..', 'backend', 'game_data.db'))
        cursor = conn.cursor()
        cursor.execute("UPDATE player_stats SET strength = ?, dexterity = ? WHERE session_id = ?", (10, 10, self.session_id))
        conn.commit()
        conn.close()

    def tearDown(self):
        # Cleanup if needed
        pass

    def test_auto_resolution(self):
        """Test Auto-Success and Auto-Failure logic."""
        print("\nTesting Auto-Resolution...")
        
        # Case 1: Trivial (Stat 20 vs DC 10) -> Margin +10 -> Auto Success
        result = self.gsm.should_require_roll(10, 20)
        self.assertEqual(result["outcome"], "auto_success")
        self.assertFalse(result["requires_roll"])
        print(f"  [Pass] Stat 20 vs DC 10 -> {result['outcome']}")

        # Case 2: Impossible (Stat 8 vs DC 18) -> Margin -10 -> Auto Failure
        result = self.gsm.should_require_roll(18, 8)
        self.assertEqual(result["outcome"], "auto_failure")
        self.assertFalse(result["requires_roll"])
        print(f"  [Pass] Stat 8 vs DC 18 -> {result['outcome']}")

        # Case 3: Uncertain (Stat 12 vs DC 15) -> Margin -3 -> Roll Needed
        result = self.gsm.should_require_roll(15, 12)
        self.assertEqual(result["outcome"], "uncertain")
        self.assertTrue(result["requires_roll"])
        print(f"  [Pass] Stat 12 vs DC 15 -> {result['outcome']}")

    def test_failure_severity(self):
        """Test Failure Severity Calculation."""
        print("\nTesting Failure Severity...")
        
        # Minor: Miss by 1-3
        # Roll 10 + Stat 2 = 12 vs DC 15 (Margin -3)
        severity = self.gsm.calculate_failure_severity(12, 15, is_crit_fail=False)
        self.assertEqual(severity, "minor")
        print(f"  [Pass] Margin -3 -> {severity}")

        # Major: Miss by 4-8
        # Roll 5 + Stat 2 = 7 vs DC 15 (Margin -8)
        severity = self.gsm.calculate_failure_severity(7, 15, is_crit_fail=False)
        self.assertEqual(severity, "major")
        print(f"  [Pass] Margin -8 -> {severity}")

        # Severe: Miss by 9+
        # Roll 2 + Stat 2 = 4 vs DC 15 (Margin -11)
        severity = self.gsm.calculate_failure_severity(4, 15, is_crit_fail=False)
        self.assertEqual(severity, "severe")
        print(f"  [Pass] Margin -11 -> {severity}")

        # Critical: Natural 1
        severity = self.gsm.calculate_failure_severity(100, 10, is_crit_fail=True)
        self.assertEqual(severity, "critical")
        print(f"  [Pass] Natural 1 -> {severity}")

    def test_relationship_updates(self):
        """Test Relationship Modifiers."""
        print("\nTesting Relationship Updates...")
        npc_name = "TestNPC"
        
        # Reset NPC
        self.gsm.update_npc_state(self.session_id, npc_name, DEFAULT_NPC_STATE.copy())
        
        # Accept Quest (+2)
        self.gsm.update_relationship(self.session_id, npc_name, "quest_accepted")
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["relationship"], 52)
        print(f"  [Pass] Accept Quest -> {state['relationship']}")

        # Minor Failure (-2)
        self.gsm.update_relationship(self.session_id, npc_name, "minor_failure")
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["relationship"], 50)
        print(f"  [Pass] Minor Failure -> {state['relationship']}")

        # Critical Failure (-15)
        self.gsm.update_relationship(self.session_id, npc_name, "critical_failure")
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["relationship"], 35)
        print(f"  [Pass] Critical Failure -> {state['relationship']}")

        # Auto Success (+5)
        self.gsm.update_relationship(self.session_id, npc_name, "auto_success")
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["relationship"], 40)
        print(f"  [Pass] Auto Success -> {state['relationship']}")

    def test_quest_spam_prevention(self):
        """Test Quest Cooldowns."""
        print("\nTesting Quest Spam Prevention...")
        npc_name = "SpamNPC"
        
        # Reset NPC
        self.gsm.update_npc_state(self.session_id, npc_name, DEFAULT_NPC_STATE.copy())
        
        # Give 1st Quest
        self.gsm.record_quest_given(self.session_id, npc_name)
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["quests_given_recently"], 1)
        
        # Give 2nd Quest
        self.gsm.record_quest_given(self.session_id, npc_name)
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["quests_given_recently"], 2)
        
        print(f"  [Pass] Quests Given Recently: {state['quests_given_recently']}")
        
        # Test Decay (Mocking time would be ideal, but we can call decay directly with a modified timestamp)
        # Manually set last_quest_given to 2 hours ago
        import time
        state["last_quest_given"] = time.time() - 7200 
        self.gsm.update_npc_state(self.session_id, npc_name, state)
        
        # Trigger decay via get_npc_state
        state = self.gsm.get_npc_state(self.session_id, npc_name)
        self.assertEqual(state["quests_given_recently"], 0) # Should decay by 2
        print(f"  [Pass] Decay after 2 hours -> {state['quests_given_recently']}")

    def test_tier_config(self):
        """Test Entity Tier Configuration."""
        print("\nTesting Entity Tiers...")
        
        # Test Average Tier
        config = self.gsm.get_tier_config("average")
        self.assertEqual(config["dc_range"], (10, 12))
        print(f"  [Pass] Average Tier DC Range: {config['dc_range']}")

        # Test Boss Tier
        config = self.gsm.get_tier_config("boss")
        self.assertEqual(config["dc_range"], (22, 25))
        print(f"  [Pass] Boss Tier DC Range: {config['dc_range']}")

        # Test Default (Fallback)
        config = self.gsm.get_tier_config("unknown_tier")
        self.assertEqual(config["dc_range"], (10, 12)) # Should default to average
        print(f"  [Pass] Unknown Tier defaults to Average")

    def test_economy_validation(self):
        """Test Economy Validation (Gold Capping)."""
        print("\nTesting Economy Validation...")
        
        # Case 1: Valid Reward (Average Tier, 20 Gold)
        quest_data = {
            "tier": "average",
            "rewards": {"gold": 20}
        }
        validated = self.gsm.validate_quest_rewards(quest_data)
        self.assertEqual(validated["rewards"]["gold"], 20)
        print(f"  [Pass] Average Tier (20g) -> {validated['rewards']['gold']}g (Unchanged)")

        # Case 2: Excessive Reward (Average Tier, 500 Gold)
        quest_data = {
            "tier": "average",
            "rewards": {"gold": 500}
        }
        validated = self.gsm.validate_quest_rewards(quest_data)
        self.assertEqual(validated["rewards"]["gold"], 25) # Capped at max for average (25)
        print(f"  [Pass] Average Tier (500g) -> {validated['rewards']['gold']}g (Capped)")

        # Case 3: Boss Tier (800 Gold)
        quest_data = {
            "tier": "boss",
            "rewards": {"gold": 800}
        }
        validated = self.gsm.validate_quest_rewards(quest_data)
        self.assertEqual(validated["rewards"]["gold"], 800)
        print(f"  [Pass] Boss Tier (800g) -> {validated['rewards']['gold']}g (Unchanged)")

if __name__ == '__main__':
    unittest.main()
