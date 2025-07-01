from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid
import os
from fastapi.middleware.cors import CORSMiddleware

from gemini_service import get_gemini_response
from gemini_image_generator import generate_and_save_image
import sys

# Add the project root to the sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from backend.game_state_manager import GameStateManager

import logging

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

logger.info("FastAPI application starting...")

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
    logger.info(f"Received user input: {user_input.dict()}")
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
    logger.info(f"Retrieving conversation history for session: {session_id}")
    conversation_history = game_state_manager.get_conversation_history(session_id)
    logger.info(f"Conversation history before adding user message: {conversation_history}")

    # Add personality prompt if history is short
    if len(conversation_history) < 2: # Heuristic to add prompt early
        conversation_history.insert(0, {"role": "user", "parts": [npc_info["personality_prompt"]]})
        conversation_history.insert(1, {"role": "model", "parts": ["Understood. I will now respond as " + npc_info["name"]]})

    conversation_history.append({"role": "user", "parts": [user_input.message]})
    logger.info(f"Conversation history after adding user message: {conversation_history}")

    # Get response from Gemini
    logger.info("Calling Gemini API...")
    gemini_dialogue = await get_gemini_response(conversation_history)
    logger.info(f"Received Gemini response: {gemini_dialogue}")
    conversation_history.append({"role": "model", "parts": [gemini_dialogue]})
    game_state_manager.update_conversation_history(session_id, conversation_history)
    logger.info(f"Conversation history after adding Gemini response: {conversation_history}")

    

    logger.info(f"Sending NPC response: {gemini_dialogue}")
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
    logger.info(f"Received command input: {command_input.dict()}")
    session_id = command_input.session_id

    if not game_state_manager.session_exists(session_id):
        # If the session is not found, create a new one.
        logger.info(f"Session {session_id} not found. Creating a new session.")
        game_state_manager.create_session(session_id)
        logger.info(f"Created session {session_id} for command.")

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
    logger.info(f"Received start game input: {start_input.dict()}")
    session_id = start_input.session_id or str(uuid.uuid4())
    
    if game_state_manager.session_exists(session_id):
        logger.info(f"Session {session_id} already exists. Rejoining.")
    else:
        logger.info(f"Creating new session: {session_id}")
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

@app.get("/npc_portrait/{npc_id}")
async def get_npc_portrait(npc_id: str):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    portrait_dir = os.path.join(project_root, "frontend", "portraits")
    portrait_path = os.path.join(portrait_dir, f"{npc_id}.png")

    if os.path.exists(portrait_path):
        logger.info(f"Serving existing portrait for {npc_id} from {portrait_path}")
        return {"portrait_url": f"/portraits/{npc_id}.png"}
    
    logger.info(f"Generating portrait for {npc_id}...")
    npc_info = game_state_manager.get_npc_info(npc_id)
    if not npc_info:
        raise HTTPException(status_code=404, detail="NPC not found.")
    
    # Construct a detailed prompt for image generation
    image_prompt = (
        f"A portrait of {npc_info['name']}, a {npc_info['race']} {npc_info['occupation']}. "
        f"Description: {npc_info['description']}. "
        f"The image should be a 3d rendered, high-quality, realistic portrait suitable for a fantasy RPG."
    )
    
    success = generate_and_save_image(image_prompt, portrait_path)
    if not success:
        raise HTTPException(status_code=500, detail="Error generating or saving portrait.")
            
    return {"portrait_url": f"/portraits/{npc_id}.png"}

@app.get("/health")
async def health_check():
    logger.info("Health check endpoint hit.")
    return {"status": "ok"}