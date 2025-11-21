import os
from google import genai

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("GEMINI_API_KEY not set.")
    exit(1)

client = genai.Client(api_key=API_KEY)

print("Listing models...")
try:
    # Attempt to list models. The method might be different in the new SDK.
    # Based on typical google-genai patterns:
    for m in client.models.list():
        print(f"Model: {m.name}")
        if "flash" in m.name:
            print(f"  -> Found Flash model: {m.name}")
except Exception as e:
    print(f"Error listing models: {e}")
