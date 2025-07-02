import google.generativeai as genai

try:
    from google.generativeai.types import Part, Content
    print("Successfully imported Part and Content from google.generativeai.types")
    
    # Attempt to create a simple Content object
    test_content = Content(role="user", parts=[Part.from_text("Hello")])
    print(f"Successfully created Content object: {test_content}")

except AttributeError as e:
    print(f"AttributeError: {e}")
    print("It seems google.generativeai.types does not have 'Part' or 'Content' directly.")
    print("Let's try accessing them via genai.types.")
    try:
        test_content = genai.types.Content(role="user", parts=[genai.types.Part.from_text("Hello")])
        print(f"Successfully created Content object using genai.types: {test_content}")
    except AttributeError as e_inner:
        print(f"Inner AttributeError: {e_inner}")
        print("Still unable to access Part or Content. This is unexpected.")
except Exception as e:
    print(f"An unexpected error occurred: {type(e).__name__}: {e}")
