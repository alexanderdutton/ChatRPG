import os
import google.genai as genai
from google.genai import types
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

# Configure the Gemini API key from environment variables
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client(api_key=API_KEY)

async def get_gemini_response(conversation_history: List[Dict]) -> str:
    try:
        formatted_history = []
        for entry in conversation_history:
            # Ensure each part is a types.Part object with a 'text' attribute
            formatted_parts = [types.Part(text=part_text) for part_text in entry["parts"]]
            formatted_history.append(types.Content(role=entry["role"], parts=formatted_parts))
        response = client.models.generate_content(model="gemini-1.5-flash-latest", contents=formatted_history)
        return response.text
    except Exception as e:
        print(f"Error calling Gemini API for text generation: {type(e).__name__}: {e}")
        return "I'm sorry, I seem to be having trouble responding right now."
