from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import os
from fastapi.middleware.cors import CORSMiddleware

from gemini_service import get_gemini_response, generate_image_from_text
import sys
import os

# Add the project root to the sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from backend.game_state_manager import GameStateManager

import logging
import os

# Configure logging to a file
log_file_path = os.path.join(os.path.dirname(__file__), "server.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler() # Also log to console
    ]
)

logger = logging.getLogger(__name__)

print("FastAPI application starting...")

app = FastAPI(
    title="Gemini NPC Dialogue Game",
    description="A simple interactive game where Gemini provides NPC dialogue."
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001"],  # Specific origins
    allow_credentials=True,
    allow_methods=["*"]  # Allows all methods
)

# Initialize game state manager (in-memory for simplicity, consider a DB for production)
game_state_manager = GameStateManager()

# --- Pydantic Models ---

class UserInput(BaseModel):
    session_id: str # Now required
    message: str

class CommandInput(BaseModel):
    session_id: str # Now required
    command: str

class NPCResponse(BaseModel):
    session_id: str
    dialogue: str
    player_name: str
    current_location: str
    inventory: List[str]
    health: int
    gold: int
    quest_log: List[str]
    map_display: str
    character_portrait: Optional[str] = None
    npcs_in_location: Optional[List[Dict]] = None

# --- API Endpoints ---

@app.post("/interact", response_model=NPCResponse)
async def interact_with_npc(user_input: UserInput):
    print(f"Received user input: {user_input.dict()}")
    session_id = user_input.session_id
    
    if not game_state_manager.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found. Please start a new game.")

    session = game_state_manager.sessions[session_id]
    npc_id = session.get("conversation_partner")

    if not npc_id:
        raise HTTPException(status_code=400, detail="Not in a conversation.")

    npc_info = game_state_manager.get_npc_info(npc_id)
    if not npc_info:
        raise HTTPException(status_code=404, detail="NPC not found.")

    # Retrieve conversation history for context
    print(f"Retrieving conversation history for session: {session_id}")
    conversation_history = game_state_manager.get_conversation_history(session_id)
    print(f"Conversation history before adding user message: {conversation_history}")

    # Add personality prompt if history is short
    if len(conversation_history) < 2: # Heuristic to add prompt early
        conversation_history.insert(0, {"role": "user", "parts": [npc_info["personality_prompt"]]})
        conversation_history.insert(1, {"role": "model", "parts": ["Understood. I will now respond as " + npc_info["name"]]})

    conversation_history.append({"role": "user", "parts": [user_input.message]})
    print(f"Conversation history after adding user message: {conversation_history}")

    # Get response from Gemini
    print("Calling Gemini API...")
    gemini_dialogue = await get_gemini_response(conversation_history)
    print(f"Received Gemini response: {gemini_dialogue}")
    conversation_history.append({"role": "model", "parts": [gemini_dialogue]})
    game_state_manager.update_conversation_history(session_id, conversation_history)
    print(f"Conversation history after adding Gemini response: {conversation_history}")

    

    print(f"Sending NPC response: {gemini_dialogue}")
    return NPCResponse(
        session_id=session_id,
        dialogue=gemini_dialogue,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location(session_id),
        inventory=game_state_manager.get_inventory(session_id),
        health=game_state_manager.get_health(session_id),
        gold=game_state_manager.get_gold(session_id),
        quest_log=game_state_manager.get_quest_log(session_id),
        map_display=game_state_manager.get_map_display(session_id),
        character_portrait=f"/portraits/{npc_id}.png"
    )




@app.post("/command", response_model=NPCResponse)
async def handle_command(command_input: CommandInput):
    print(f"Received command input: {command_input.dict()}")
    session_id = command_input.session_id

    if not game_state_manager.session_exists(session_id):
        # If the session is not found, create a new one.
        print(f"Session {session_id} not found. Creating a new session.")
        game_state_manager.create_session(session_id)
        print(f"Created session {session_id} for command.")

    command_response_text = game_state_manager.process_command(session_id, command_input.command)

    npcs_in_location = game_state_manager.get_npcs_in_location(session_id)

    return NPCResponse(
        session_id=session_id,
        dialogue=command_response_text,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location(session_id),
        inventory=game_state_manager.get_inventory(session_id),
        health=game_state_manager.get_health(session_id),
        gold=game_state_manager.get_gold(session_id),
        quest_log=game_state_manager.get_quest_log(session_id),
        map_display=game_state_manager.get_map_display(session_id),
        npcs_in_location=npcs_in_location
    )


class StartGameInput(BaseModel):
    session_id: Optional[str] = None
    player_name: Optional[str] = 'adventurer'


@app.post("/start", response_model=NPCResponse)
async def start_game(start_input: StartGameInput):
    session_id = start_input.session_id or str(uuid.uuid4())
    
    if game_state_manager.session_exists(session_id):
        print(f"Session {session_id} already exists. Rejoining.")
    else:
        print(f"Creating new session: {session_id}")
        game_state_manager.create_session(session_id, player_name=start_input.player_name)

    initial_dialogue = "Welcome to the world of Eldoria, a land of mystery and adventure. Your journey begins now. You find yourself in the bustling town square of Silverhaven. What would you like to do?"
    
    npcs_in_location = game_state_manager.get_npcs_in_location(session_id)

    return NPCResponse(
        session_id=session_id,
        dialogue=initial_dialogue,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location(session_id),
        inventory=game_state_manager.get_inventory(session_id),
        health=game_state_manager.get_health(session_id),
        gold=game_state_manager.get_gold(session_id),
        quest_log=game_state_manager.get_quest_log(session_id),
        map_display=game_state_manager.get_map_display(session_id),
        npcs_in_location=npcs_in_location
    )

@app.get("/get_portrait/{npc_id}")
async def get_portrait(npc_id: str):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    portrait_dir = os.path.join(project_root, "frontend", "portraits")
    portrait_path = os.path.join(portrait_dir, f"{npc_id}.png")
    
    # if not os.path.exists(portrait_path): # Temporarily disabled for debugging
    print(f"Generating portrait for {npc_id}...")
    npc_info = game_state_manager.get_npc_info(npc_id)
    if not npc_info:
        raise HTTPException(status_code=404, detail="NPC not found.")
    
    image_prompt = f"A portrait of {npc_info['name']}, a {npc_info['race']} {npc_info['occupation']} in a fantasy setting. {npc_info['description']}"
    try:
        image_data = await generate_image_from_text(image_prompt)
        os.makedirs(os.path.dirname(portrait_path), exist_ok=True)
        with open(portrait_path, "wb") as f:
            f.write(image_data)
        print(f"Portrait saved to {portrait_path}")
    except Exception as e:
        print(f"Error generating or saving portrait: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating portrait: {e}")
        
    return {"portrait_url": f"/portraits/{npc_id}.png"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}