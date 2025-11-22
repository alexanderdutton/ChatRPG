
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world, game_world

async def test_memory_update_flow():
    print("Initializing Game World...")
    initialize_game_world()
    print(f"Game World Characters: {game_world.characters.keys()}")
    
    print("Initializing GameStateManager...")
    gsm = GameStateManager()
    session_id = "test_session_memory_debug_v2"
    
    # 1. Create Session
    gsm.create_session(session_id)
    print(f"Session {session_id} created.")
    
    # 2. Set up Interaction
    npc_name = "Brom" # Assuming Brom exists
    # Need to find Brom's ID or just set it manually if we can
    # Let's assume we are in Oakhaven where Brom is.
    gsm.set_current_location_name(session_id, "Oakhaven")
    
    # Get NPC ID for Brom
    npcs = gsm.get_npcs_in_location(session_id)
    print(f"NPCs in Oakhaven: {npcs}")
    brom_id = next((npc['id'] for npc in npcs if npc['name'] == "Brom"), None)
    
    if not brom_id:
        print("Error: Brom not found in Oakhaven. Using first available NPC or creating dummy.")
        if npcs:
            brom_id = npcs[0]['id']
        else:
            print("No NPCs found. Cannot proceed.")
            return

    print(f"Found NPC ID: {brom_id}")
    
    # 3. Start Interaction
    gsm.set_conversation_partner(session_id, brom_id)
    gsm.set_game_mode(session_id, "INTERACTION")
    print("Interaction started.")
    
    # 4. Add some history
    history = [
        {"role": "user", "parts": ["Hello Brom"]},
        {"role": "model", "parts": ["Hello Traveler"]}
    ]
    gsm.update_conversation_history(session_id, history)
    print("History added.")
    
    # 5. Simulate "Leave" command flow from main.py
    print("\n--- Simulating 'Leave' Command Flow ---")
    
    # Step A: Capture Pre-State (as done in main.py)
    pre_partner = gsm.get_conversation_partner(session_id)
    pre_history = gsm.get_conversation_history(session_id)
    
    print(f"Pre-Partner: {pre_partner}")
    print(f"Pre-History Len: {len(pre_history) if pre_history else 0}")
    
    # Step B: Process Command (which calls end_interaction)
    command = "leave"
    response = await gsm.process_command(session_id, command)
    print(f"Process Command Response: {response}")
    
    # Step C: Verify State After Command
    post_partner = gsm.get_conversation_partner(session_id)
    post_history = gsm.get_conversation_history(session_id) # Should be same if not cleared yet?
    # Wait, end_interaction sets mode to EXPLORATION and partner to None.
    # Does it clear history? No, archive_conversation does that.
    
    print(f"Post-Partner: {post_partner}")
    print(f"Post-History Len: {len(post_history) if post_history else 0}")
    
    # Step D: Check Condition (from main.py)
    condition = (command in ["leave", "exit", "bye", "goodbye"] and pre_partner and pre_history)
    print(f"Condition Met: {condition}")
    
    if condition:
        print("SUCCESS: Memory update would be triggered.")
    else:
        print("FAILURE: Memory update would NOT be triggered.")

if __name__ == "__main__":
    asyncio.run(test_memory_update_flow())
