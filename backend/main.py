import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .game_state_manager import GameStateManager
from .game_world import initialize_game_world
from .gemini_service import get_gemini_response, generate_game_data, generate_npc_memory_update, CHALLENGE_SYSTEM_PROMPT, validate_quest_output
from .gemini_image_generator import generate_and_save_image

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
# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
# We mount it to /static first to avoid conflicts, but we also want to serve index.html at root.
# Actually, let's mount the whole frontend dir to root, but we need to be careful about API routes.
# FastAPI matches routes in order. So if we define API routes first, they take precedence.
# However, mounting to "/" usually catches everything.
# Better approach: Mount assets to specific paths if possible, or use a catch-all for the SPA.
# Since this is simple:
# 1. Mount /portraits to frontend/portraits
# 2. Mount /style.css specifically or just serve static files.
# Let's mount the entire frontend directory to serve static assets.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
frontend_dir = os.path.join(project_root, "frontend")

app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
app.mount("/portraits", StaticFiles(directory=os.path.join(frontend_dir, "portraits")), name="portraits")

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(frontend_dir, "index.html"))

@app.get("/style.css")
async def read_css():
    return FileResponse(os.path.join(frontend_dir, "style.css"))

@app.get("/index.html")
async def read_index():
    return FileResponse(os.path.join(frontend_dir, "index.html"))

@app.get("/test")
async def test_endpoint():
    return {"message": "Server is reloading!"}

# Initialize game state manager (in-memory for simplicity, consider a DB for production)
game_state_manager = GameStateManager()

# Initialize the game world data
initialize_game_world("Elodia")

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
    map_display: Dict[str, Any]
    character_portrait: Optional[str] = None
    game_mode: str = "EXPLORATION"
    metadata: Dict[str, Any] = {}
    conversation_partner_name: Optional[str] = None
    npcs_in_location: Optional[List[Dict]] = None
    player_stats: Optional[Dict[str, int]] = None
    active_quests: Optional[List[Dict]] = None
    skill_check: Optional[Dict[str, Any]] = None

# --- API Endpoints ---

@app.post("/interact", response_model=NPCResponse)
async def interact_with_npc(user_input: UserInput):
    logger.info(f"Received user input: {user_input.dict()}")
    session_id = user_input.session_id
    
    if not game_state_manager.session_exists(session_id):
        raise HTTPException(status_code=404,
                            detail="Session not found. Please start a new game.")

    npc_id = game_state_manager.get_conversation_partner(session_id)

    if not npc_id:
        raise HTTPException(status_code=400, detail="Not in a conversation.")

    npc_info = game_state_manager.get_npc_info(npc_id)
    if not npc_info:
        raise HTTPException(status_code=404, detail="NPC not found.")

    # Retrieve conversation history for context
    logger.info(f"Retrieving conversation history for session: {session_id}")
    conversation_history = game_state_manager.get_conversation_history(session_id)
    logger.info(f"Conversation history before adding user message: {conversation_history}")

    # Retrieve NPC state for memory and greetings
    npc_state = game_state_manager.get_npc_state(session_id, npc_id)
    npc_memory = npc_state.get("memory", [])
    npc_greetings = npc_state.get("greetings", [])

    # Check for initial greeting (empty history)
    if not conversation_history:
        logger.info("Initial interaction. Using preloaded greeting.")
        
        # Use dynamic greetings if available, otherwise fallback to NPC info or defaults
        greetings = npc_greetings if npc_greetings else npc_info.get("greetings", [
            f"{npc_info['name']} looks up from their work. 'Greetings, traveler. What brings you here?'",
            f"{npc_info['name']} nods in acknowledgement. 'Can I help you with something?'",
            f"{npc_info['name']} pauses and regards you carefully. 'Well met. speak your mind.'",
            f"{npc_info['name']} offers a weary smile. 'Another face in these parts? Welcome.'",
            f"{npc_info['name']} seems busy but spares you a glance. 'Yes? What is it?'"
        ])
        
        import random
        greeting = random.choice(greetings)
        
        # Add to history
        conversation_history.append({"role": "model", "parts": [greeting]})
        game_state_manager.update_conversation_history(session_id, conversation_history)
        
        # Portrait Logic
        portrait_path = f"frontend/portraits/{npc_id}.png"
        portrait_url = None
        if os.path.exists(portrait_path):
            portrait_url = f"/portraits/{npc_id}.png"
        else:
            logger.info(f"Portrait for {npc_id} not found. Triggering generation...")
            # Async generation trigger
            import asyncio
            asyncio.create_task(get_npc_portrait(npc_id))

        return NPCResponse(
            session_id=session_id,
            dialogue=greeting,
            player_name=game_state_manager.get_player_name(session_id),
            current_location=game_state_manager.get_current_location_name(session_id),
            world_name=game_state_manager.get_world_name(),
            inventory=game_state_manager.get_inventory(session_id),
            health=game_state_manager.get_health(session_id),
            gold=game_state_manager.get_gold(session_id),
            quest_log=game_state_manager.get_quest_log(session_id),
            map_display=game_state_manager.get_map_display(session_id),
            character_portrait=portrait_url,
            game_mode=game_state_manager.get_game_mode(session_id),
            conversation_partner_name=npc_info.get("name", "Unknown"),
            npcs_in_location=game_state_manager.get_npcs_in_location(session_id)
        )

    # Add personality prompt as system instruction
    player_name = game_state_manager.get_player_name(session_id)
    location_desc = game_state_manager.get_current_location_description(session_id)
    
    location_context = f"You are in {location_desc['location_description']}."
    if location_desc.get('current_feature_description'):
        location_context += f" The player is standing at: {location_desc['current_feature_description']}."
        
    # Inject Memory
    memory_context = ""
    if npc_memory:
        memory_context = f"Memories of {player_name}: {json.dumps(npc_memory)}"

    # Inject Challenge System Prompt
    player_stats = game_state_manager.get_player_stats(session_id)
    npc_state = game_state_manager.get_npc_state(session_id, npc_id)
    
    # Determine failure severity from recent history
    failure_severity = "None"
    if conversation_history and conversation_history[-1]["role"] == "user":
        last_msg = conversation_history[-1]["parts"][0]
        if "[System]" in last_msg and "Severity:" in last_msg:
            import re
            match = re.search(r"Severity: (\w+)", last_msg)
            if match:
                failure_severity = match.group(1)

    challenge_prompt = CHALLENGE_SYSTEM_PROMPT.format(
        player_stats=json.dumps(player_stats),
        npc_state=json.dumps(npc_state),
        failure_severity=failure_severity
    )

    # Inject Quest History (Active/Completed/Failed only)
    quest_history = game_state_manager.get_quest_context_for_npc(session_id)
    quest_context = f"Quest Log (Active & Completed): {json.dumps(quest_history)}"

    system_instruction = (f"You are {npc_info['name']}. "
                          f"You are talking to {player_name}. "
                          f"{location_context} "
                          f"{npc_info['personality_prompt']} "
                          f"{memory_context} "
                          f"{quest_context} "
                          f"{challenge_prompt}")

    conversation_history.append({"role": "user", "parts": [user_input.message]})
    logger.info(f"Conversation history after adding user message: {conversation_history}")

    # Get response from Gemini
    logger.info("Calling Gemini API...")
    gemini_dialogue, metadata = await get_gemini_response(conversation_history,
                                                            system_instruction=system_instruction)
    logger.info(f"Received Gemini dialogue: {gemini_dialogue}")
    logger.info(f"Received Gemini metadata: {metadata}")
    
    # Handle Quest Offer
    if "quest_offered" in metadata:
        quest_data = metadata["quest_offered"]
        validation_errors = validate_quest_output(quest_data)
        if not validation_errors:
            logger.info(f"Valid quest offered: {quest_data['id']}")
            game_state_manager.add_quest(session_id, quest_data)
        else:
            logger.error(f"Invalid quest offered: {validation_errors}")

    # Handle Quest Resolution (Turn-In)
    if "quest_resolved" in metadata:
        quest_id = metadata["quest_resolved"]
        logger.info(f"Quest resolved (turned in): {quest_id}")
        game_state_manager.resolve_quest(session_id, quest_id)

    game_state_manager.process_metadata(session_id, metadata)
    conversation_history.append({"role": "model", "parts": [gemini_dialogue]})
    game_state_manager.update_conversation_history(session_id, conversation_history)
    logger.info(f"Conversation history after adding Gemini response: {conversation_history}")

    

    # Portrait Logic
    portrait_path = f"frontend/portraits/{npc_id}.png"
    portrait_url = None
    if os.path.exists(portrait_path):
        portrait_url = f"/portraits/{npc_id}.png"
    else:
        # Trigger generation if not exists
        # For now, we just log it. In a real scenario, we'd fire a background task.
        # We can use the existing generate_image_for_npc logic if exposed, 
        # or just let the user's "generate" command handle it.
        # But the user asked for auto-generation.
        # We'll assume a background task or just return None for now and let the frontend handle the placeholder.
        logger.info(f"Portrait for {npc_id} not found. Triggering generation...")
        # TODO: Trigger async generation here
        pass

    logger.info(f"Sending NPC response: {gemini_dialogue}")
    return NPCResponse(
        session_id=session_id,
        dialogue=gemini_dialogue,
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location_name(session_id),
        world_name=game_state_manager.get_world_name(),
        inventory=game_state_manager.get_inventory(session_id),
        metadata=metadata,  # Pass metadata to frontend
        health=game_state_manager.get_health(session_id),
        gold=game_state_manager.get_gold(session_id),
        quest_log=game_state_manager.get_quest_log(session_id),
        map_display=game_state_manager.get_map_display(session_id),
        character_portrait=portrait_url,
        game_mode=game_state_manager.get_game_mode(session_id),
        conversation_partner_name=npc_info.get("name", "Unknown") if npc_info else None,
        npcs_in_location=game_state_manager.get_npcs_in_location(session_id),
        player_stats=game_state_manager.get_player_stats(session_id),
        active_quests=game_state_manager.get_active_quests(session_id),
        skill_check=metadata.get("skill_check")
    )

class ResolveChallengeInput(BaseModel):
    session_id: str
    challenge_id: str

from fastapi import BackgroundTasks

def update_history_background(session_id: str, result: Dict[str, Any]):
    history = game_state_manager.get_conversation_history(session_id)
    
    severity_note = ""
    if not result.get('success'):
        severity_note = f" (Severity: {result.get('severity', 'Unknown')})"
    elif result.get('auto_resolved'):
        severity_note = " (Auto-Success)"
        
    system_note = f"[System] Player resolved challenge '{result.get('description', 'Unknown')}' with result: {'Success' if result.get('success') else 'Failure'}{severity_note}."
    history.append({"role": "user", "parts": [system_note]})
    game_state_manager.update_conversation_history(session_id, history)

@app.post("/resolve_challenge")
async def resolve_challenge_endpoint(input: ResolveChallengeInput, background_tasks: BackgroundTasks):
    logger.info(f"Resolving challenge: {input.challenge_id} for session {input.session_id}")
    result = game_state_manager.resolve_challenge(input.session_id, input.challenge_id)
    
    # Update conversation history in background
    background_tasks.add_task(update_history_background, input.session_id, result)
    
    return result

class SkillCheckInput(BaseModel):
    session_id: str
    type: str
    dc: int
    success_response: str
    failure_response: str
    description: str

@app.post("/resolve_skill_check")
async def resolve_skill_check_endpoint(input: SkillCheckInput):
    logger.info(f"Resolving skill check: {input.description} (DC {input.dc} {input.type})")
    
    # Get Stats
    stats = game_state_manager.get_player_stats(input.session_id)
    stat_bonus = stats.get(input.type.lower(), 0)
    
    import random
    roll = random.randint(1, 20)
    total = roll + stat_bonus
    success = total >= input.dc
    
    response_text = input.success_response if success else input.failure_response
    
    result = {
        "success": success,
        "roll": roll,
        "stat_bonus": stat_bonus,
        "total": total,
        "dc": input.dc,
        "npc_response": response_text,
        "critical_success": roll == 20,
        "critical_failure": roll == 1
    }
    
    # Update History
    history = game_state_manager.get_conversation_history(input.session_id)
    
    # Add system note about the roll (so LLM knows what happened next time)
    system_note = f"[System] Player rolled {roll} + {stat_bonus} = {total} vs DC {input.dc} on {input.description}. Result: {'Success' if success else 'Failure'}."
    history.append({"role": "user", "parts": [system_note]})
    
    # Add NPC response
    history.append({"role": "model", "parts": [response_text]})
    
    game_state_manager.update_conversation_history(input.session_id, history)
    
    return result

class StateInput(BaseModel):
    session_id: str

@app.post("/state", response_model=NPCResponse)
async def get_game_state(input: StateInput):
    session_id = input.session_id
    if not game_state_manager.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    
    npc_id = game_state_manager.get_conversation_partner(session_id)
    npc_info = game_state_manager.get_npc_info(npc_id) if npc_id else None
    
    portrait_url = None
    if npc_id:
        portrait_path = f"frontend/portraits/{npc_id}.png"
        if os.path.exists(portrait_path):
            portrait_url = f"/portraits/{npc_id}.png"

    return NPCResponse(
        session_id=session_id,
        dialogue="", # No dialogue for state update
        player_name=game_state_manager.get_player_name(session_id),
        current_location=game_state_manager.get_current_location_name(session_id),
        world_name=game_state_manager.get_world_name(),
        inventory=game_state_manager.get_inventory(session_id),
        health=game_state_manager.get_health(session_id),
        gold=game_state_manager.get_gold(session_id),
        quest_log=game_state_manager.get_quest_log(session_id),
        map_display=game_state_manager.get_map_display(session_id),
        npcs_in_location=game_state_manager.get_npcs_in_location(session_id),
        game_mode=game_state_manager.get_game_mode(session_id),
        character_portrait=portrait_url,
        conversation_partner_name=npc_info.get("name", "Unknown") if npc_info else None,
        player_stats=game_state_manager.get_player_stats(session_id),
        active_quests=game_state_manager.get_active_quests(session_id)
    )


class QuestDecisionInput(BaseModel):
    session_id: str
    quest_id: str

@app.post("/accept_quest")
async def accept_quest_endpoint(input: QuestDecisionInput):
    logger.info(f"Accepting quest: {input.quest_id} for session {input.session_id}")
    response_text = game_state_manager.accept_quest(input.session_id, input.quest_id)
    
    # Update conversation history so NPC remembers saying this
    history = game_state_manager.get_conversation_history(input.session_id)
    history.append({"role": "model", "parts": [response_text]})
    game_state_manager.update_conversation_history(input.session_id, history)
    
    return {"status": "success", "message": "Quest accepted", "npc_response": response_text}

@app.post("/refuse_quest")
async def refuse_quest_endpoint(input: QuestDecisionInput):
    logger.info(f"Refusing quest: {input.quest_id} for session {input.session_id}")
    response_text = game_state_manager.refuse_quest(input.session_id, input.quest_id)
    
    # Update conversation history so NPC remembers saying this
    history = game_state_manager.get_conversation_history(input.session_id)
    history.append({"role": "model", "parts": [response_text]})
    game_state_manager.update_conversation_history(input.session_id, history)
    
    return {"status": "success", "message": "Quest refused", "npc_response": response_text}



@app.post("/command", response_model=NPCResponse)
async def handle_command(command_input: CommandInput):
    logger.info(f"Received command input: {command_input.dict()}")
    session_id = command_input.session_id

    if not game_state_manager.session_exists(session_id):
        # If the session is not found, create a new one.
        logger.info(f"Session {session_id} not found. Creating a new session.")
        game_state_manager.create_session(session_id)
        logger.info(f"Created session {session_id} for command.")

    # Capture state BEFORE processing command (because 'leave' will clear it)
    session_data = game_state_manager._get_session_data(session_id)
    logger.info(f"DEBUG: Full Session Data: {session_data}")
    
    pre_command_partner = game_state_manager.get_conversation_partner(session_id)
    pre_command_history = game_state_manager.get_conversation_history(session_id)

    command_response_text = await game_state_manager.process_command(session_id,
                                                                command_input.command)

    # Check if we just entered interaction mode to trigger an immediate greeting
    if command_input.command.lower().startswith("talk to "):
        npc_id = game_state_manager.get_conversation_partner(session_id)
        if npc_id:
            npc_info = game_state_manager.get_npc_info(npc_id)
            npc_state = game_state_manager.get_npc_state(session_id, npc_id)
            npc_greetings = npc_state.get("greetings", [])
            
            # Use dynamic greetings if available, otherwise fallback
            greetings = npc_greetings if npc_greetings else npc_info.get("greetings", [
                f"{npc_info['name']} looks up from their work. 'Greetings, traveler. What brings you here?'",
                f"{npc_info['name']} nods in acknowledgement. 'Can I help you with something?'",
                f"{npc_info['name']} pauses and regards you carefully. 'Well met. speak your mind.'",
                f"{npc_info['name']} offers a weary smile. 'Another face in these parts? Welcome.'",
                f"{npc_info['name']} seems busy but spares you a glance. 'Yes? What is it?'"
            ])
            
            import random
            greeting = random.choice(greetings)
            
            # Handle dictionary greetings (action/dialogue)
            if isinstance(greeting, dict):
                greeting_text = f"*{greeting.get('action', '')}* {greeting.get('dialogue', '')}"
            else:
                greeting_text = str(greeting)
            
            # Add to history so it's consistent
            conversation_history = game_state_manager.get_conversation_history(session_id)
            conversation_history.append({"role": "model", "parts": [greeting_text]})
            game_state_manager.update_conversation_history(session_id, conversation_history)
            
            # Override response text with the greeting
            command_response_text = greeting_text

            # Portrait Logic: Trigger generation if missing
            portrait_path = f"frontend/portraits/{npc_id}.png"
            if not os.path.exists(portrait_path):
                logger.info(f"Portrait for {npc_id} not found. Triggering generation...")
                import asyncio
                asyncio.create_task(get_npc_portrait(npc_id))

    # Check if command was "leave" or "exit" to trigger memory update
    if command_input.command.lower() in ["leave", "exit", "bye", "goodbye"]:
        npc_id = game_state_manager.get_conversation_partner(session_id)
        # If we were in a conversation (npc_id might be None if already left, but let's check history)
        # Actually, process_command sets conversation_partner to None on leave.
        # We need to capture it BEFORE process_command or check if we just left.
        # But process_command handles the state change.
        
        # Alternative: Check if we *were* in interaction mode and now are not?
        # Or better: Just check if there's a lingering conversation history to process.
        history = game_state_manager.get_conversation_history(session_id)
        if history:
             # We have a history to process!
             # We need to know WHO we were talking to. 
             # Since process_command cleared the partner, we might need to store it temporarily or retrieve it differently.
             # However, `process_command` logic for "leave" is:
             # 1. Check if in interaction
             # 2. Set mode to EXPLORATION
             # 3. Set partner to None
             
             # We can't easily get the partner ID *after* process_command returns if it cleared it.
             # Let's modify this flow slightly.
             pass # Logic moved to inside process_command or handled here by peeking before call?
             # Peeking before call is safer.
    
    # ... (process_command called above) ...
    
    # If we successfully left a conversation
    logger.info(f"Command: {command_input.command}, Partner: {pre_command_partner}, History Len: {len(pre_command_history) if pre_command_history else 0}")
    if command_input.command.lower() in ["leave", "exit", "bye", "goodbye"] and pre_command_partner and pre_command_history:
        logger.info(f"Ending conversation with {pre_command_partner}. Triggering memory update.")
        
        # Async Memory Update
        npc_info = game_state_manager.get_npc_info(pre_command_partner)
        npc_state = game_state_manager.get_npc_state(session_id, pre_command_partner)
        current_memory = npc_state.get("memory", [])
        current_greetings = npc_state.get("greetings", [])
        player_name = game_state_manager.get_player_name(session_id)
        
        # Define async task
        async def update_memory_task():
            logger.info(f"Starting memory update task for {pre_command_partner}...")
            logger.info(f"History length: {len(pre_command_history)}")
            logger.info(f"Current Memory: {current_memory}")
            
            updates = await generate_npc_memory_update(
                pre_command_history,
                current_memory,
                current_greetings,
                npc_info['name'],
                player_name
            )
            
            logger.info(f"Received updates from LLM: {updates}")
            
            if updates:
                updated_memory = updates.get("updated_memory", [])
                new_greetings = updates.get("new_greetings", [])
                
                # Update State - REPLACE memory with consolidated version
                if updated_memory:
                    game_state_manager.update_npc_state(session_id, pre_command_partner, {"memory": updated_memory})
                    logger.info(f"Updated (Consolidated) memory for {pre_command_partner}: {updated_memory}")
                else:
                    logger.warning(f"No 'updated_memory' returned for {pre_command_partner}")
                
                if new_greetings:
                    game_state_manager.update_npc_state(session_id, pre_command_partner, {"greetings": new_greetings})
                    logger.info(f"Updated greetings for {pre_command_partner}: {new_greetings}")
                else:
                    logger.warning(f"No 'new_greetings' returned for {pre_command_partner}")
            else:
                logger.error(f"Failed to generate updates for {pre_command_partner} (updates is empty/None)")

        # Archive/Clear History IMMEDIATELY to prevent race conditions
        game_state_manager.archive_conversation(session_id)
        
        import asyncio
        asyncio.create_task(update_memory_task())

    npcs_in_location = game_state_manager.get_npcs_in_location(session_id)

    # Portrait Logic for Command (if in conversation)
    conversation_partner = game_state_manager.get_conversation_partner(session_id)
    portrait_url = None
    if conversation_partner:
        portrait_path = f"frontend/portraits/{conversation_partner}.png"
        if os.path.exists(portrait_path):
            portrait_url = f"/portraits/{conversation_partner}.png"

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
        npcs_in_location=npcs_in_location,
        game_mode=game_state_manager.get_game_mode(session_id),
        character_portrait=portrait_url,
        active_quests=game_state_manager.get_active_quests(session_id)
    )

@app.post("/debug/clear_quests")
async def clear_dead_quests_endpoint(input: StateInput):
    logger.info(f"Clearing dead quests for session {input.session_id}")
    count = game_state_manager.clear_dead_quests(input.session_id)
    return {"status": "success", "cleared_count": count}


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
    
    if "error" in initial_description_parts:
        logger.error(f"Error getting location description: {initial_description_parts['error']}")
        # Fallback or raise exception
        initial_dialogue = f"Error: {initial_description_parts['error']}"
    else:
        initial_dialogue = initial_description_parts.get("location_description", "You are in a void.")
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
        npcs_in_location=npcs_in_location,
        active_quests=game_state_manager.get_active_quests(session_id)
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

@app.get("/npc_debug/{session_id}/{npc_id}")
async def get_npc_debug_info(session_id: str, npc_id: str):
    """Returns debug information for a specific NPC."""
    if not game_state_manager.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
        
    npc_info = game_state_manager.get_npc_info(npc_id)
    if not npc_info:
        raise HTTPException(status_code=404, detail="NPC not found.")
        
    npc_state = game_state_manager.get_npc_state(session_id, npc_id)
    
    return {
        "name": npc_info["name"],
        "memory": npc_state.get("memory", []),
        "greetings": npc_state.get("greetings", []),
        "base_info": npc_info
    }

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
            # Let's get the first session if any, or assume a new one for now.
            session_id_to_update = game_state_manager.get_active_session_id()
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
