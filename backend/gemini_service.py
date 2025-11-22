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

# Print module paths for debugging
print(f"Path for google.generativeai: {genai.__file__}")
print(f"Path for google.generativeai.types: {genai.types.__file__}")

def extract_json_metadata(text: str) -> Tuple[str, Dict[str, Any]]:
    """Extracts a JSON object from a string and returns the remaining text and
    the parsed JSON."""
    json_pattern = re.compile(r"```json(.*?)```", re.DOTALL)
    match = json_pattern.search(text)
    
    metadata = {}
    dialogue = text

    if match:
        json_str = match.group(1).strip()
        try:
            metadata = json.loads(json_str)
            dialogue = json_pattern.sub("", text).strip()  # Remove the JSON block from the dialogue
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
        2. Summarize this into a concise memory string (or multiple strings).
        3. Evaluate the current greetings. Create 2-3 NEW, narrative-style greetings.
           - Each greeting should include a brief action describing what the NPC is doing when the player arrives.
           - The dialogue should reference recent events or the current relationship state.
           - Format: [Action description] "{npc_name} says: [Dialogue]"
           - Example: Brom wipes soot from his brow and nods. "Ah, {player_name}. Did you find that ore I asked for?"
        
        Output JSON format:
        {{
            "memory_update": ["memory string 1", "memory string 2"],
            "new_greetings": ["greeting 1", "greeting 2", "greeting 3"]
        }}
        
        If nothing important happened, return empty list for memory_update.
        ALWAYS return new_greetings to keep them fresh.
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