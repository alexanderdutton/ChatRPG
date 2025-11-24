import os
from google import genai
from google.genai import types
from typing import List, Dict, Tuple, Any
import logging
import json
import re
from .models import GameWorldData
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Configure the Gemini API key from environment variables
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client(api_key=API_KEY)

def extract_json_metadata(text: str) -> Tuple[str, Dict[str, Any]]:
    """Extracts a JSON object from a string and returns the remaining text and
    the parsed JSON."""
    # 1. Try strict markdown with json tag
    json_pattern = re.compile(r"```json(.*?)```", re.DOTALL)
    match = json_pattern.search(text)
    
    # 2. Try generic markdown
    if not match:
        json_pattern = re.compile(r"```(.*?)```", re.DOTALL)
        match = json_pattern.search(text)

    # 3. Try raw JSON (first { to last })
    if not match:
        json_pattern = re.compile(r"(\{.*\})", re.DOTALL)
        match = json_pattern.search(text)

    metadata = {}
    dialogue = text

    if match:
        json_str = match.group(1).strip()
        try:
            metadata = json.loads(json_str)
            # If the JSON contains a 'dialogue' field, use it as the primary dialogue
            if "dialogue" in metadata:
                dialogue = metadata["dialogue"]
            else:
                # Otherwise, remove the JSON block from the original text
                dialogue = json_pattern.sub("", text).strip()
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to decode JSON from Gemini response: {e}"
            )
            # If JSON is malformed, treat the whole thing as dialogue
            metadata = {}

    return dialogue, metadata

async def get_gemini_response(conversation_history: List[Dict],
                                system_instruction: str = None) -> Tuple[str, Dict[str, Any]]:
    try:
        formatted_history = []
        for entry in conversation_history:
            formatted_parts = [
                types.Part(text=part_text)
                for part_text in entry["parts"]
            ]
            formatted_history.append(types.Content(role=entry["role"], parts=formatted_parts))

        config = None
        if system_instruction:
            config = types.GenerateContentConfig(system_instruction=system_instruction)

        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=formatted_history,
            config=config
        )
        
        dialogue, metadata = extract_json_metadata(response.text)
        return dialogue, metadata
    except Exception as e:
        logger.error(
            f"Error calling Gemini API for text generation: "
            f"{type(e).__name__}: {e}"
        )
        return "I'm sorry, I seem to be having trouble responding right now.", {}

async def generate_game_data(request: str) -> Dict[str, Any]:
    """Generates new game data using the Gemini API and the prompt template.
    """
    try:
        # Generate the prompt template dynamically from the Pydantic model
        json_schema_template = GameWorldData.schema_json(indent=2)
        prompt_template = f"""
You are a creative assistant for a text-based adventure game. Your task is to generate data for a new game world, including locations and characters. The output must be in JSON format, following the template provided below. You must always include at least one location, and each location must have a 'characters' array, even if it's empty. Each location must also have an ASCII map with a key, an initial player starting point, and at least one entrance/exit.

**Game World Template (JSON Schema):**

```json
{json_schema_template}
```

Please generate new game data based on the following request:

[INSERT REQUEST HERE]
"""
        # Fill in the request
        prompt = prompt_template.replace("[INSERT REQUEST HERE]", request)

        # Call the Gemini API
        generation_config = types.GenerationConfig(
            temperature=0.7,
            top_p=0.95,
            top_k=40,
            max_output_tokens=2048
        )
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT",
             "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",
             "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
             "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT",
             "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # Extract and return the JSON data
        _, metadata = extract_json_metadata(response.text)
        try:
            validated_data = GameWorldData.parse_obj(metadata)
            return validated_data.dict()
        except ValidationError as e:
            logger.error(f"Generated game data failed Pydantic validation: {e}")
            return {}
    except Exception as e:
        logger.error(
            f"Error calling Gemini API for game data generation: "
            f"{type(e).__name__}: {e}"
        )
        return {}

async def generate_item_details(item_name: str) -> str:
    """Generates a creative description for an item."""
    try:
        prompt = f"""
        Describe the item '{item_name}' for a fantasy RPG.
        Include its appearance, potential magical properties, and a bit of lore.
        Keep it concise (under 100 words).
        """
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating item details: {e}")
        return f"A simple {item_name}."

async def generate_quest(context: str) -> str:
    """Generates a dynamic quest based on the current context."""
    try:
        prompt = f"""
        Generate a short, engaging quest hook for a fantasy RPG player.
        Context: {context}
        The quest should be something they can start immediately.
        Keep it concise (under 50 words).
        """
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating quest: {e}")
        return "You hear rumors of trouble nearby, but nothing specific."

async def generate_npc_memory_update(conversation_history: List[Dict], 
                                     current_memory: List[str], 
                                     current_greetings: List[str],
                                     npc_name: str,
                                     player_name: str) -> Dict[str, Any]:
    """Generates a memory update and new greetings for an NPC based on a conversation."""
    try:
        # Format conversation for the prompt
        formatted_convo = ""
        for entry in conversation_history:
            role = "Player" if entry["role"] == "user" else npc_name
            text = " ".join(entry["parts"])
            formatted_convo += f"{role}: {text}\n"

        prompt = f"""
        Analyze the following conversation between {npc_name} and {player_name}.
        
        Current Memory of {npc_name}: {json.dumps(current_memory)}
        Current Greetings: {json.dumps(current_greetings)}
        
        Conversation:
        {formatted_convo}
        
        Task:
        1. Identify any significant new information {npc_name} learned about {player_name} or the world.
        2. REWRITE the "Current Memory" list into a new, consolidated list.
           - Combine old memories with new information.
           - Remove obsolete or redundant details.
           - Keep the list concise (aim for 5-10 key facts).
        3. Evaluate the current greetings. Create 2-3 NEW, narrative-style greetings.
           - Each greeting should include a brief action describing what the NPC is doing when the player arrives.
           - The dialogue should reference recent events or the current relationship state.
           - Format: [Action description] "{npc_name} says: [Dialogue]"
           - Example: Brom wipes soot from his brow and nods. "Ah, {player_name}. Did you find that ore I asked for?"
        
        Output JSON format:
        {{
            "updated_memory": ["consolidated memory 1", "consolidated memory 2"],
            "new_greetings": ["greeting 1", "greeting 2", "greeting 3"]
        }}
        
        ALWAYS return updated_memory (even if it's just the old memory) and new_greetings.
        """
        
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        
        _, metadata = extract_json_metadata(response.text)
        return metadata
    except Exception as e:
        logger.error(f"Error generating NPC memory update: {e}")
        return {}

CHALLENGE_SYSTEM_PROMPT = """
QUEST GENERATION RULES:
IMPORTANT: Not every conversation needs a quest!

Only offer a quest if:
1. The NPC's mood is "worried" or "desperate" (not "content" or "happy")
2. The player explicitly asks for work/help/quests (PLAYER OVERRIDE)
3. A major event has occurred that requires player involvement
4. relationship_level > 50 AND quests_given_recently < 2

PLAYER OVERRIDE: If the player explicitly says:
- "Do you have any work?"
- "I'm looking for quests"
- "Need help with anything?"

Then you MAY offer a quest even if mood is "content", BUT:
- Keep it optional/low-pressure
- Frame it as "Well, if you're offering..."
- Don't make it urgent unless mood justifies it

Otherwise, if mood is "content" and player is just chatting:
- DO NOT offer quests unprompted
- Engage in conversation, lore, or relationship building
- Comment on completed quests

FAILING FORWARD RULES:
When the player fails a challenge, respond based on failure severity:

MINOR FAILURE (missed DC by 1-3):
- Partial success: Task is done but imperfect
- Example: "The beam is crooked, but it'll hold."
- Relationship impact: -2

MAJOR FAILURE (missed DC by 4-8):
- Alternative needed: Current approach doesn't work
- Example: "This beam is too heavy for you. Maybe try a pulley?"
- Relationship impact: -5
- Suggest different approach or help

SEVERE FAILURE (missed DC by 9+):
- Quest abandoned: NPC gives up on player help
- Example: "Never mind, I'll hire a professional."
- Relationship impact: -10
- Quest marked as "withdrawn"

CRITICAL FAILURE (Natural 1):
- Negative outcome: Things get worse
- Example: "You snapped the beam! Now I need two!"
- Relationship impact: -15
- May spawn follow-up consequence

NEVER respond with:
- "Try again"
- "Go get more materials and come back"
- Endless retry loops

Current failure severity: {failure_severity}

CHALLENGE GENERATION FORMAT:
When the player asks for a quest or you determine the NPC has a task:

1. IF you are offering a quest or giving a task, you MUST output a JSON object wrapped in markdown code blocks (```json ... ```).
   The structure MUST be EXACTLY:
{{
  "dialogue": "Your spoken response here (e.g. 'I need your help with...')",
  "quest_offered": {{
    "id": "unique_quest_id",
    "description": "Brief quest description",
    "giver_npc": "NPC Name",
    "accept_response": "What you will say if the player accepts the quest (e.g., 'Excellent! I knew I could count on you.')",
    "refuse_response": "What you will say if the player refuses the quest (e.g., 'A pity. Perhaps another time.')",
    "challenges": [
        {{
            "id": "challenge_id_1",
            "type": "strength|dexterity|intelligence|charisma",
            "difficulty": "easy|medium|hard|heroic",
            "dc": 10|15|20|25,
            "description": "Description of the specific challenge (e.g. 'Break down the door')"
        }}
    ],
    "involved_entities": ["entity_id_1", "entity_id_2"]
  }}
}}

2. Difficulty to DC mapping:
   - easy: DC 10
   - medium: DC 15
   - hard: DC 20
   - heroic: DC 25

3. Choose challenge_type based on the quest nature:
   - strength: Physical force, combat, breaking things
   - dexterity: Stealth, agility, precision
   - intelligence: Puzzles, knowledge, investigation
   - charisma: Persuasion, deception, social interaction

4. ONLY include entities that are:
   - Already established in the world
   - OR being introduced by this quest (mark in involved_entities)

CURRENT PLAYER STATS:
{player_stats}

CURRENT NPC STATE:
{npc_state}

IMPORTANT: Do not invent new attributes for existing entities. 
Stick to established facts from the world data.

- **quest_resolved**: (Optional) The ID of a quest that the player has successfully turned in or resolved during this interaction. Use this ONLY when the player explicitly reports completion to the quest giver.

- **skill_check**: (Optional) A JSON object representing an immediate skill check during conversation.
    - **type**: One of "strength", "dexterity", "intelligence", "charisma".
    - **difficulty**: "easy" (DC 10), "medium" (DC 15), "hard" (DC 20), "heroic" (DC 25).
    - **dc**: The Difficulty Class integer.
    - **description**: Description of the check (e.g., "Persuade the guard").
    - **success_response**: What you will say if the player succeeds (e.g., "Very well, you make a compelling point.").
    - **failure_response**: What you will say if the player fails (e.g., "I don't think so. Move along.").

IMPORTANT: You MUST output valid JSON. If you are offering a quest, the 'quest_offered' field is mandatory. If resolving a quest, 'quest_resolved' is mandatory. Do not just write the dialogue.
"""

def validate_quest_output(quest_data: Dict[str, Any]) -> List[str]:
    """Returns list of validation errors, empty if valid."""
    errors = []
    
    # Check for required fields in Quest
    required_quest = ["id", "description", "challenges", "accept_response", "refuse_response"]
    for field in required_quest:
        if field not in quest_data:
            errors.append(f"Missing required field in quest: {field}")
            
    if "challenges" in quest_data:
        for challenge in quest_data["challenges"]:
            # Check difficulty matches DC
            dc_map = {"easy": 10, "medium": 15, "hard": 20, "heroic": 25}
            difficulty = challenge.get("difficulty")
            dc = challenge.get("dc")
            
            if difficulty and dc and dc != dc_map.get(difficulty):
                errors.append(f"DC {dc} doesn't match difficulty level {difficulty}")
            
            # Check challenge type is valid
            valid_types = ["strength", "dexterity", "intelligence", "charisma"]
            if challenge.get("type") not in valid_types:
                errors.append(f"Invalid challenge type: {challenge.get('type')}")
            
            # Check for required fields in Challenge
            required_challenge = ["id", "type", "dc", "description"]
            for field in required_challenge:
                if field not in challenge:
                    errors.append(f"Missing required field in challenge: {field}")
    
    return errors