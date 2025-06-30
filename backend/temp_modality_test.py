import google.generativeai as genai

try:
    print(hasattr(genai.types, 'Modality'))
except Exception as e:
    print(f'Error inspecting genai.types: {e}')
