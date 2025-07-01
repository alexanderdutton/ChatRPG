import os
import logging
from PIL import Image
import io
import base64
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

def generate_and_save_image(prompt: str, output_path: str) -> bool:
    try:
        client = genai.Client()
        
        logger.info(f"Attempting to generate image for prompt: '{prompt}' using Gemini API.")
        
        response = client.models.generate_content(
            model="gemini-2.0-flash-preview-image-generation",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=['TEXT', 'IMAGE']
            )
        )

        image_part = None
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_part = part
                break
        
        if not image_part:
            logger.warning("Gemini API image generation returned no image data.")
            return False

        img_byte_arr = io.BytesIO(image_part.inline_data.data)
        img = Image.open(img_byte_arr)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path)
        logger.info(f"Image saved to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error during Gemini API image generation or saving: {type(e).__name__}: {e}")
        return False