import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from game_state_manager import GameStateManager
from game_world import initialize_game_world
from gemini_service import get_gemini_response, generate_game_data
from gemini_image_generator import generate_and_save_image

# Add the project root to the sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# Configure logging to a file with rotation
log_file_path = os.path.join(os.path.dirname(__file__), "server.log")

# Create a TimedRotatingFileHandler
# Rotates daily (when='midnight'), keeps 7 backup files
file_handler = TimedRotatingFileHandler(log_file_path, when="midnight", interval=1, backupCount=7)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Configure the root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        file_handler,
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
    world_name: str
    inventory: List[str]
    health: int
    gold: int
    quest_log: List[str]
    map_display: Optional[Dict] = None
    character_portrait: Optional[str] = None
    npcs_in_location: Optional[List[Dict]] = None

# --- API Endpoints ---

@app.post("/interact", response_model=NPCResponse)
async def interact_with_npc(user_input: UserInput):
    logger.info(f"Received user input: {user_input.dict()}")
    session_id = user_input.session_id
    
    if not game_state_manager.session_exists(session_id):
        raise HTTPException(status_code=404,
                            detail="Session not found. Please start a new game.")

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
    # Add personality prompt as system instruction
    system_instruction = (f"You are {npc_info['name']}. "
                          f"{npc_info['personality_prompt']}")

    conversation_history.append({"role": "user", "parts": [user_input.message]})
    logger.info(f"Conversation history after adding user message: {conversation_history}")

    # Get response from Gemini
    logger.info("Calling Gemini API...")
    gemini_dialogue, metadata = await get_gemini_response(conversation_history,
                                                            system_instruction=system_instruction)
    logger.info(f"Received Gemini dialogue: {gemini_dialogue}")
    logger.info(f"Received Gemini metadata: {metadata}")
    game_state_manager.process_metadata(session_id, metadata)
    conversation_history.append({"role": "model", "parts": [gemini_dialogue]})
    game_state_manager.update_conversation_history(session_id, conversation_history)
    logger.info(f"Conversation history after adding Gemini response: {conversation_history}")

    

    logger.info(f"Sending NPC response: {gemini_dialogue}")
    return NPCResponse(
        session_id=session_id,
        dialogue=gemini_dialogue,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location_name(session_id),
        world_name=game_state_manager.get_world_name(),
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

    command_response_text = game_state_manager.process_command(session_id,
                                                                command_input.command)

    npcs_in_location = game_state_manager.get_npcs_in_location(session_id)

    return NPCResponse(
        session_id=session_id,
        dialogue=command_response_text,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location_name(session_id),
        world_name=game_state_manager.get_world_name(),
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
        # Ensure the default world is loaded when a new session is created
        initialize_game_world("Elodia")
        game_state_manager.create_session(session_id,
                                          player_name=start_input.player_name)

    initial_description_parts = game_state_manager.get_current_location_description(session_id)
    initial_dialogue = initial_description_parts["location_description"]
    if initial_description_parts.get("current_feature_description"):
        initial_dialogue += f"\n{initial_description_parts['current_feature_description']}"
    if initial_description_parts.get("adjacent_features_description"):
        initial_dialogue += f"\n{initial_description_parts['adjacent_features_description']}"
    if initial_description_parts.get("npcs_description"):
        initial_dialogue += f"\n{initial_description_parts['npcs_description']}"
    
    npcs_in_location = game_state_manager.get_npcs_in_location(session_id)

    return NPCResponse(
        session_id=session_id,
        dialogue=initial_dialogue,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location_name(session_id),
        world_name=game_state_manager.get_world_name(),
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
        f"The image should be a 3d rendered, high-quality, realistic portrait "         f"suitable for a fantasy RPG."
    )
    
    success = generate_and_save_image(image_prompt, portrait_path)
    if not success:
        raise HTTPException(status_code=500,
                            detail="Error generating or saving portrait.")
            
    return {"portrait_url": f"/portraits/{npc_id}.png"}

@app.get("/health")
async def health_check():
    logger.info("Health check endpoint hit.")
    return {"status": "ok"}

class LoadWorldInput(BaseModel):
    world_name: str

@app.post("/load_world")
async def load_world(input: LoadWorldInput):
    logger.info(f"Received request to load world: {input.world_name}")
    try:
        first_location_name = initialize_game_world(input.world_name)
        if first_location_name:
            # Assuming a session exists or creating a new one for the loaded world
            # This might need refinement based on how sessions are managed
            # For now, let's assume we have a session_id to work with.
            # We might need to pass session_id from frontend or get it from current context.
            # For simplicity, let's assume a default session or the last active one.
            # This assumes there's always an active session when load_world is called.
            # If not, we'd need to create one or handle the error.
            # Let's get the first session if any, or assume a new one for now.
            session_id_to_update = list(game_state_manager.sessions.keys())[0] if game_state_manager.sessions else None
            if session_id_to_update:
                game_state_manager.set_current_location_name(session_id_to_update, first_location_name)
                logger.info(f"Updated session {session_id_to_update} to location: {first_location_name}")
            else:
                logger.warning("No active session found to update after loading world.")

        return {"status": "success",
                "message": f"World '{input.world_name}' loaded successfully."}
    except FileNotFoundError:
        raise HTTPException(status_code=404,
                            detail=f"World '{input.world_name}' not found.")
    except Exception as e:
        logger.error(f"Error loading world {input.world_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading world: {e}")

class GenerateDataInput(BaseModel):
    request: str

@app.get("/list_worlds")
async def list_worlds():
    worlds_dir = os.path.join(os.path.dirname(__file__), "worlds")
    if not os.path.exists(worlds_dir):
        return {"worlds": []}
    world_files = [f for f in os.listdir(worlds_dir) if f.endswith(".json")]
    return {"worlds": [os.path.splitext(f)[0] for f in world_files]}

@app.post("/generate_data")
async def generate_data(input: GenerateDataInput):
    logger.info(f"Received data generation request: {input.request}")
    game_data_from_gemini = await generate_game_data(input.request)

    if not game_data_from_gemini:
        raise HTTPException(status_code=500,
                            detail="Failed to generate game data.")

    # Generate a unique filename for the new world
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    world_name = f"world_{timestamp}"
    world_file_path = os.path.join(os.path.dirname(__file__), "worlds",
                                   f"{world_name}.json")

    # Save the new world data to a distinct file
    with open(world_file_path, 'w') as f:
        json.dump(game_data_from_gemini, f, indent=4)
    logger.info(f"Saved new world data to {world_file_path}")

    # Re-initialize the game world to load the new data (this will load the newly generated world)
    initialize_game_world(world_name)  # Pass the world name to initialize_game_world

    return {"status": "success",
            "message": f"New world '{world_name}' generated and loaded."}
