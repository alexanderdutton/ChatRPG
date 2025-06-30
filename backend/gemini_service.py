import os
import google.genai as genai
from typing import List, Dict
from PIL import Image
import io
import base64

# Configure the Gemini API key from environment variables
# It's crucial to never hardcode API keys directly in your code.
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

genai.configure(api_key=API_KEY)

# Initialize the Gemini model for text generation
text_model = genai.GenerativeModel('models/gemini-1.5-flash-latest')

# Initialize the Gemini model for image generation (if a specific one is available)
# Note: As of my last update, direct image generation from text via a dedicated model
# might require a different approach or a specific model name if available.
# For now, we'll assume a general model can handle image generation requests
# or that a future API update will provide a dedicated one.
# If a dedicated image generation model is not available, this function might need adjustment
# to use a text-to-image API or a different service.
# Note: As of my last update, direct image generation from text via a dedicated model
# might require a different approach or a specific model name if available.
# For now, we'll assume a general model can handle image generation requests
# or that a future API update will provide a dedicated one.
# If a dedicated image generation model is not available, this function might need adjustment
# to use a text-to-image API or a different service.
image_model = genai.GenerativeModel('gemini-2.0-flash-preview-image-generation')

async def get_gemini_response(conversation_history: List[Dict]) -> str:
    """
    Sends a conversation history to the Gemini API and returns the model's response.
    """
    try:
        response = await text_model.generate_content_async(conversation_history)
        return response.text
    except Exception as e:
        print(f"Error calling Gemini API for text generation: {type(e).__name__}: {e}")
        return "I'm sorry, I seem to be having trouble responding right now."

async def generate_image_from_text(prompt: str) -> bytes:
    """
    Generates an image from a text prompt using the Gemini API and returns the image data as bytes.
    """
    try:
        response = await image_model.generate_content_async(
            contents=prompt,
            response = await image_model.generate_content_async(
            contents=prompt,
            config={'response_modalities': ['TEXT', 'IMAGE']}
        )
        )
        
        if not response.candidates:
            print("Gemini API response has no candidates.")
            raise ValueError("No candidates in Gemini API response.")

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                mime_type = part.inline_data.mime_type
                if mime_type.startswith("image/"):
                    print(f"Successfully generated image from Gemini API. Mime type: {mime_type}")
                    decoded_image_data = base64.b64decode(part.inline_data.data)
                    try:
                        img = Image.open(io.BytesIO(decoded_image_data))
                        # You can add more validation here if needed, e.g., img.format
                        print(f"Image successfully opened with PIL. Format: {img.format}, Mode: {img.mode}, Size: {img.size}")
                        # Return the raw decoded bytes, as the saving is handled in main.py
                        return decoded_image_data
                    except Exception as e:
                        print(f"Error opening image data with PIL: {e}")
                        raise ValueError("Received data is not a valid image format.")
                else:
                    print(f"Gemini API returned inline data with unexpected mime type: '{mime_type}'. Full inline_data: {part.inline_data}")
                    raise ValueError("Gemini API returned unexpected inline data type.")
            elif part.text is not None:
                print(f"Gemini API returned text instead of image: {part.text}")
                raise ValueError("Gemini API returned text instead of image data.")
        
        print("Gemini API response did not contain expected image data in any part.")
        raise ValueError("No image data in Gemini API response.")

    except Exception as e:
        print(f"Error calling Gemini API for image generation: {type(e).__name__}: {e}")
        # Return a placeholder image in case of failure
        img = Image.new('RGB', (60, 30), color = 'gray') # Error placeholder image
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()