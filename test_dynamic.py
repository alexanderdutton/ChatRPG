import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world

async def test_dynamic():
    print("Initializing Game World...")
    initialize_game_world("Elodia")

    print("Initializing GameStateManager...")
    gsm = GameStateManager()
    session_id = "test_dynamic_session"
    gsm.create_session(session_id, "TestPlayer")

    # Test Inspect (need an item first)
    print("Testing Inspect...")
    gsm.add_item_to_inventory(session_id, "Rusty Dagger")
    response = await gsm.process_command(session_id, "inspect Rusty Dagger")
    print(f"Inspect Response: {response}")
    
    if "Rusty Dagger" in response or "dagger" in response.lower():
        print("Inspect: OK (Response seems relevant)")
    else:
        print("Inspect: WARNING (Response might be generic)")

    # Test Rumors
    print("Testing Rumors...")
    response = await gsm.process_command(session_id, "rumors")
    print(f"Rumors Response: {response}")
    
    if len(response) > 10:
        print("Rumors: OK")
    else:
        print("Rumors: WARNING (Response too short)")

if __name__ == "__main__":
    asyncio.run(test_dynamic())
