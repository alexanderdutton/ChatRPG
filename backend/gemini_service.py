import os
from google import genai
from google.genai import types
from typing import List, Dict, Tuple, Any
import logging
import json
import re

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
            model="gemini-1.5-flash-latest",
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
        # Load the prompt template
        template_path = os.path.join(os.path.dirname(__file__),
                                     "gemini_prompt_template.txt")
        with open(template_path, 'r') as f:
            prompt_template = f.read()

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
            model="gemini-1.5-flash-latest",
            contents=prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # Extract and return the JSON data
        _, metadata = extract_json_metadata(response.text)
        return metadata
    except Exception as e:
        logger.error(
            f"Error calling Gemini API for game data generation: "
            f"{type(e).__name__}: {e}"
        )
        return {}