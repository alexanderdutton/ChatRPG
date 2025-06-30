import os
import google.generativeai as genai
 
# Configure the Gemini API key from environment variables
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
     raise ValueError("GEMINI_API_KEY environment variable not set.")
 
 genai.configure(api_key=API_KEY)
 
 try:
     # Initialize a simple Gemini model
     model = genai.GenerativeModel('models/gemini-1.5-pro-latest') # Or emini-pro' if you prefer
   14 
   15     # Make a very simple request
   16     response = model.generate_content("Hello, Gemini!")
   17     print("Successfully received response from Gemini API:")
   18     print(response.text)
   19 
   20 except Exception as e:
   21     print(f"Error during direct Gemini API call: {type(e).__name__}: {e}")