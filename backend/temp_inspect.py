import google.generativeai as genai

try:
    if hasattr(genai.types, 'GenerationConfig'):
        print('GenerationConfig exists in genai.types')
    else:
        print('GenerationConfig does NOT exist in genai.types')
except Exception as e:
    print(f'Error inspecting genai.types: {e}')