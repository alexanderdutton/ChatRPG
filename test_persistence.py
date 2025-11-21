import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world

def test_persistence():
    print("Initializing Game World...")
    initialize_game_world("Elodia")

    print("Initializing GameStateManager (Run 1)...")
    gsm1 = GameStateManager()
    session_id = "test_session_123"
    
    # Create session if not exists
    if not gsm1.session_exists(session_id):
        gsm1.create_session(session_id, "TestPlayer")
        print("Session created.")
    else:
        print("Session already exists (from previous run).")

    # Modify state
    print("Modifying state...")
    gsm1.set_gold(session_id, 500)
    gsm1.add_item_to_inventory(session_id, "Magic Sword")
    gsm1.set_current_location_name(session_id, "Village Square") # Assuming this location exists in Elodia

    # Verify state in Run 1
    gold = gsm1.get_gold(session_id)
    inventory = gsm1.get_inventory(session_id)
    print(f"Run 1 - Gold: {gold}, Inventory: {inventory}")
    assert gold == 500
    assert "Magic Sword" in inventory

    print("-" * 20)
    print("Re-initializing GameStateManager (Run 2 - Simulating Restart)...")
    # In a real restart, the object is destroyed. Here we just make a new one.
    gsm2 = GameStateManager()
    
    # Verify state persists
    gold_2 = gsm2.get_gold(session_id)
    inventory_2 = gsm2.get_inventory(session_id)
    location_2 = gsm2.get_current_location_name(session_id)
    
    print(f"Run 2 - Gold: {gold_2}, Inventory: {inventory_2}, Location: {location_2}")
    
    if gold_2 == 500 and "Magic Sword" in inventory_2:
        print("SUCCESS: Persistence verified!")
    else:
        print("FAILURE: State did not persist correctly.")
        exit(1)

if __name__ == "__main__":
    # Clean up previous test db if needed, but we want to test persistence so maybe not?
    # For this test, we want to verify write then read.
    # We can delete the db file first to ensure a clean start.
    db_path = os.path.join(os.path.dirname(__file__), "../backend/game_data.db")
    if os.path.exists(db_path):
        os.remove(db_path)
        print("Deleted existing DB for clean test.")
        
    test_persistence()
