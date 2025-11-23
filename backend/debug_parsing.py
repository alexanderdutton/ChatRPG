
import re
import json
import logging

# Mock logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_json_metadata(text: str):
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
            # If the JSON contains a 'dialogue' field, use it as the primary dialogue
            if "dialogue" in metadata:
                dialogue = metadata["dialogue"]
            else:
                dialogue = json_pattern.sub("", text).strip()
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to decode JSON from Gemini response: {e}"
            )
            metadata = {}

    return dialogue, metadata

# Test Cases
test_cases = [
    # Case 1: Standard correct format
    """Here is the quest.
```json
{
  "dialogue": "I need help.",
  "quest_offered": {"id": "q1"}
}
```""",
    
    # Case 2: No backticks (Common LLM failure)
    """{
  "dialogue": "I need help.",
  "quest_offered": {"id": "q1"}
}""",

    # Case 3: Backticks but no 'json' tag
    """```
{
  "dialogue": "I need help.",
  "quest_offered": {"id": "q1"}
}
```""",

    # Case 4: Text after JSON
    """```json
{
  "dialogue": "I need help.",
  "quest_offered": {"id": "q1"}
}
```
Hope you accept!""",

    # Case 5: Malformed JSON
    """```json
{
  "dialogue": "I need help.",
  "quest_offered": {"id": "q1"
}
```"""
]

for i, case in enumerate(test_cases):
    print(f"--- Case {i+1} ---")
    d, m = extract_json_metadata(case)
    print(f"Dialogue: {d}")
    print(f"Metadata: {m}")
    print()
