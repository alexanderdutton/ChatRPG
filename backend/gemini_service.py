import os
from google import genai
from google.genai import types
from typing import List, Dict, Tuple, Any
import logging
import json
import re
from .models import GameWorldData
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Configure the Gemini API key from environment variables
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client(api_key=API_KEY)

def extract_json_metadata(text: str) -> Tuple[str, Dict[str, Any]]:
    """Extracts a JSON object from a string and returns the remaining text and
    the parsed JSON."""
    # 1. Try strict markdown with json tag
    json_pattern = re.compile(r"```json(.*?)```", re.DOTALL)
    match = json_pattern.search(text)
    
    # 2. Try generic markdown
    if not match:
        json_pattern = re.compile(r"```(.*?)```", re.DOTALL)
        match = json_pattern.search(text)

    # 3. Try raw JSON (first { to last })
    if not match:
        json_pattern = re.compile(r"(\{.*\})", re.DOTALL)
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
                # Otherwise, remove the JSON block from the original text
                dialogue = json_pattern.sub("", text).strip()
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
            model="gemini-flash-latest",
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
        # Generate the prompt template dynamically from the Pydantic model
        json_schema_template = GameWorldData.schema_json(indent=2)
        prompt_template = f"""
You are a creative assistant for a text-based adventure game. Your task is to generate data for a new game world, including locations and characters. The output must be in JSON format, following the template provided below. You must always include at least one location, and each location must have a 'characters' array, even if it's empty. Each location must also have an ASCII map with a key, an initial player starting point, and at least one entrance/exit.

**Game World Template (JSON Schema):**

```json
{json_schema_template}
```

Please generate new game data based on the following request:

[INSERT REQUEST HERE]
"""
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
            model="gemini-flash-latest",
            contents=prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # Extract and return the JSON data
        _, metadata = extract_json_metadata(response.text)
        try:
            validated_data = GameWorldData.parse_obj(metadata)
            return validated_data.dict()
        except ValidationError as e:
            logger.error(f"Generated game data failed Pydantic validation: {e}")
            return {}
    except Exception as e:
        logger.error(
            f"Error calling Gemini API for game data generation: "
            f"{type(e).__name__}: {e}"
        )
        return {}

async def generate_item_details(item_name: str) -> str:
    """Generates a creative description for an item."""
    try:
        prompt = f"""
        Describe the item '{item_name}' for a fantasy RPG.
        Include its appearance, potential magical properties, and a bit of lore.
        Keep it concise (under 100 words).
        """
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating item details: {e}")
        return f"A simple {item_name}."

async def generate_quest(context: str) -> str:
    """Generates a dynamic quest based on the current context."""
    try:
        prompt = f"""
        Generate a short, engaging quest hook for a fantasy RPG player.
        Context: {context}
        The quest should be something they can start immediately.
        Keep it concise (under 50 words).
        """
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating quest: {e}")
        return "You hear rumors of trouble nearby, but nothing specific."

async def generate_npc_memory_update(conversation_history: List[Dict], 
                                     current_memory: List[str], 
                                     current_greetings: List[str],
                                     npc_name: str,
                                     player_name: str) -> Dict[str, Any]:
    """Generates a memory update and new greetings for an NPC based on a conversation."""
    try:
        # Format conversation for the prompt
        formatted_convo = ""
        for entry in conversation_history:
            role = "Player" if entry["role"] == "user" else npc_name
            text = " ".join(entry["parts"])
            formatted_convo += f"{role}: {text}\n"

        prompt = f"""
        Analyze the following conversation between {npc_name} and {player_name}.
        
        Current Memory of {npc_name}: {json.dumps(current_memory)}
        Current Greetings: {json.dumps(current_greetings)}
        
        Conversation:
        {formatted_convo}
        
        Task:
        1. Identify any significant new information {npc_name} learned about {player_name} or the world.
        2. REWRITE the "Current Memory" list into a new, consolidated list.
           - Combine old memories with new information.
           - Remove obsolete or redundant details.
           - Keep the list concise (aim for 5-10 key facts).
        3. Evaluate the current greetings. Create 2-3 NEW, narrative-style greetings.
           - Each greeting should include a brief action describing what the NPC is doing when the player arrives.
           - The dialogue should reference recent events or the current relationship state.
           - Format: [Action description] "{npc_name} says: [Dialogue]"
           - Example: Brom wipes soot from his brow and nods. "Ah, {player_name}. Did you find that ore I asked for?"
        
        Output JSON format:
        {{
            "updated_memory": ["consolidated memory 1", "consolidated memory 2"],
            "new_greetings": ["greeting 1", "greeting 2", "greeting 3"]
        }}
        
        ALWAYS return updated_memory (even if it's just the old memory) and new_greetings.
        """
        
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        
        _, metadata = extract_json_metadata(response.text)
        return metadata
    except Exception as e:
        logger.error(f"Error generating NPC memory update: {e}")
        return {}

ANTI_YES_AND_RULES = """
CRITICAL INSTRUCTION - MECHANICAL INTEGRITY:

You are a NARRATOR, not a GAME MASTER. You do NOT decide outcomes.

FORBIDDEN ACTIONS:
❌ Letting player succeed when outcome = "failure"
❌ Reducing enemy difficulty because player tried something clever
❌ Giving player bonus stats/abilities they don't have
❌ Ignoring established entity tiers
❌ Creating new mechanical effects (damage, healing, buffs)
❌ Allowing "narrative workarounds" to mechanical failures

ALLOWED ACTIONS:
✅ Describing outcomes in flavorful ways
✅ Adding atmospheric details
✅ Giving emotional responses from NPCs
✅ Suggesting alternative approaches (after failure is narrated)

EXAMPLE - CORRECT BEHAVIOR:
Mechanical Outcome: FAILURE (player STR 12, bully DC 15, roll: 8)
Your Response: "You throw a punch, but the bully is faster. He catches your wrist and twists it painfully. You're not strong enough to overpower him."

EXAMPLE - WRONG BEHAVIOR:
Mechanical Outcome: FAILURE
Your Response: "You cleverly feint and catch him off-guard, landing a solid hit!" ← NO. This contradicts the failure.

If the player says "but I should have succeeded because..." → 
You respond: "I understand your frustration, but mechanically your Strength wasn't high enough. You could try a different approach (Dexterity to dodge?) or come back when you're stronger."

You are an impartial narrator of pre-determined outcomes.
You do NOT reward creativity with mechanical success.
Creativity gets narrative flavor, not rule-breaking.
"""

ENTITY_TIER_DESCRIPTIONS = """
ENTITY TIERS (Use these to gauge difficulty):
- Trivial: Rats, drunk peasants, children. No real threat. (DC 5-8)
- Average: Town guards, wild animals, common bandits. Fair fight. (DC 10-12)
- Tough: Veteran soldiers, dire wolves, skilled duelists. Challenging. (DC 15-17)
- Elite: Champion fighters, young dragons, master assassins. Requires prep. (DC 18-20)
- Boss: Warlords, ancient dragons, demon lords. Major climax. (DC 22-25)
- Legendary: Gods, apocalyptic threats. Campaign defining. (DC 26-30)
"""

QUEST_REWARD_RULES = """
QUEST REWARD SYSTEM:

NPC RESOURCE PROFILE:
- Resource Level: {{resource_level}}
- Can Offer: {{can_offer}}
- Cannot Offer: {{cannot_offer}}

REWARD RULES:
1. XP is automatic based on Tier (do not mention specific XP amounts).
2. Material rewards MUST match your Resource Level and the Quest Tier.
3. If you are 'destitute' or 'poor', you cannot offer gold (or very little). Offer gratitude, food, or information instead.
4. If you are 'wealthy', you can offer gold and items, but within limits.

TIER REFERENCE (Gold Ranges):
- Tier 0 (Trivial): 1-5 gold (if affordable)
- Tier 1 (Minor): 10-25 gold
- Tier 2 (Standard): 50-100 gold
- Tier 3 (Significant): 150-300 gold
- Tier 4 (Major): 500-1000 gold
- Tier 5 (Legendary): 2000-5000 gold

When offering a quest, generate a JSON with "quest_offered" containing "rewards" object with "gold" (int) and "items" (list of strings).
When the player accepts a quest, generate a JSON with "quest_accepted": "quest_id".
IMPORTANT: When accepting, DO NOT just say "Quest Accepted". Write a natural response from the NPC (e.g., "Excellent! I knew I could count on you," or "Thank you. Here are the details...").
"""

def validate_llm_quest_rewards(llm_output: Dict[str, Any], npc_profile: Dict[str, Any], calculated_rewards: Dict[str, Any]) -> List[str]:
    """
    Ensures LLM didn't promise rewards NPC can't deliver.
    """
    errors = []
    
    from .game_state_manager import NPC_RESOURCE_LEVELS
    
    resource_level_name = npc_profile.get("resource_level", "poor")
    resource_level_config = NPC_RESOURCE_LEVELS.get(resource_level_name, NPC_RESOURCE_LEVELS["poor"])
    
    quest_data = llm_output.get("quest_offered", {})
    if not quest_data:
        return []

    rewards = quest_data.get("rewards", {})
    
    # Check gold promises
    offered_gold = rewards.get("gold", 0)
    if offered_gold > 0:
        # Check if NPC can offer gold at all
        can_offer_gold = "gold_range" in resource_level_config or any("gold" in offer for offer in resource_level_config["can_offer"])
        
        if not can_offer_gold:
             errors.append(f"{npc_profile.get('name', 'NPC')} (resource level: {resource_level_name}) cannot offer gold.")
        else:
            # Check against calculated max gold
            max_gold = 0
            for r in calculated_rewards.get("material_rewards", []):
                if r.get("type") == "gold":
                    max_gold = max(max_gold, r.get("amount", 0))
            
            # Allow 50% wiggle room
            if offered_gold > max_gold * 1.5:
                errors.append(f"Gold amount too high: {offered_gold} vs calculated max {max_gold} (with 50% buffer).")

    # Check for items
    offered_items = rewards.get("items", [])
    if offered_items:
        # This is a basic check. More sophisticated validation would require
        # a detailed list of what each resource level 'can_offer' in terms of specific items.
        if "items" not in resource_level_config["can_offer"] and "valuable_items" not in resource_level_config["can_offer"]:
            errors.append(f"{npc_profile.get('name', 'NPC')} (resource level: {resource_level_name}) cannot offer items.")
        # Further validation could check if specific item names are within the NPC's capacity
        # based on a more detailed 'can_offer' list or a separate item database.
    
    return errors


CHALLENGE_SYSTEM_PROMPT = """
{ANTI_YES_AND_RULES}

{ENTITY_TIER_DESCRIPTIONS}

{QUEST_REWARD_RULES}

QUEST GENERATION RULES:
IMPORTANT: Not every conversation needs a quest!


Only offer a quest if:
1. The NPC's mood is "worried" or "desperate" (not "content" or "happy")
2. The player explicitly asks for work/help/quests (PLAYER OVERRIDE)
3. A major event has occurred that requires player involvement
4. relationship_level > 50 AND quests_given_recently < 2

PLAYER OVERRIDE: If the player explicitly says:
- "Do you have any work?"
- "I'm looking for quests"
- "Need help with anything?"

Then you MAY offer a quest even if mood is "content", BUT:
- Keep it optional/low-pressure
- Frame it as "Well, if you're offering..."
- Don't make it urgent unless mood justifies it

Otherwise, if mood is "content" and player is just chatting:
- DO NOT offer quests unprompted
- Engage in conversation, lore, or relationship building
- Comment on completed quests

FAILING FORWARD RULES:
When the player fails a challenge, respond based on failure severity:

MINOR FAILURE (missed DC by 1-3):
- Partial success: Task is done but imperfect
- Example: "The beam is crooked, but it'll hold."
- Relationship impact: -2

MAJOR FAILURE (missed DC by 4-8):
- Alternative needed: Current approach doesn't work
- Example: "This beam is too heavy for you. Maybe try a pulley?"
- Relationship impact: -5
- Suggest different approach or help

SEVERE FAILURE (missed DC by 9+):
- Quest abandoned: NPC gives up on player help
- Example: "Never mind, I'll hire a professional."
- Relationship impact: -10
- Quest marked as "withdrawn"

CRITICAL FAILURE (Natural 1):
- Negative outcome: Things get worse
- Example: "You snapped the beam! Now I need two!"
- Relationship impact: -15
- May spawn follow-up consequence (MUST BE LOWER DC or DIFFERENT STAT)
- IF player failed a high DC check, DO NOT offer another high DC check immediately.
- Offer a "redemption" task that is easier but less rewarding, or uses a different approach.

NEVER respond with:
- "Try again"
- "Go get more materials and come back"
- Endless retry loops

Current failure severity: {failure_severity}

CHALLENGE GENERATION FORMAT:
When the player asks for a quest or you determine the NPC has a task:

1. Create a JSON object with "quest_offered".
2. "challenges" is a list of steps.
3. "tier" MUST be one of: trivial, average, tough, elite, boss, legendary.

{{
    "quest_offered": {{
        "id": "quest_id_snake_case",
        "description": "Brief description of the task.",
        "giver_npc": "{{npc_name}}",
        "tier": "average", 
        "accept_response": "Great, I knew I could count on you.",
        "refuse_response": "A pity. I'll find someone else.",
        "challenges": [
            {{
                "id": "challenge_id_snake_case",
                "type": "Strength",
                "tier": "average",
                "description": "Lift the heavy beam."
            }}
        ],
        "involved_entities": ["Entity Name 1", "Entity Name 2"]
    }}
}}


2. Difficulty to DC mapping:
   - easy: DC 10
   - medium: DC 15
   - hard: DC 20
   - heroic: DC 25

3. Choose challenge_type based on the quest nature:
   - strength: Physical force, combat, breaking things
   - dexterity: Stealth, agility, precision
   - intelligence: Puzzles, knowledge, investigation
   - charisma: Persuasion, deception, social interaction

4. ONLY include entities that are:
   - Already established in the world
   - OR being introduced by this quest (mark in involved_entities)

CURRENT PLAYER STATS:
{player_stats}

CURRENT NPC STATE:
{npc_state}

IMPORTANT: Do not invent new attributes for existing entities. 
Stick to established facts from the world data.

- **quest_resolved**: (Optional) The ID of a quest that the player has successfully turned in or resolved during this interaction. Use this ONLY when the player explicitly reports completion to the quest giver.

- **skill_check**: (Optional) A JSON object representing an immediate skill check during conversation.
    - **type**: One of "strength", "dexterity", "intelligence", "charisma".
    - **difficulty**: "easy" (DC 10), "medium" (DC 15), "hard" (DC 20), "heroic" (DC 25).
    - **dc**: The Difficulty Class integer.
    - **description**: Description of the check (e.g., "Persuade the guard").
    - **success_response**: What you will say if the player succeeds (e.g., "Very well, you make a compelling point.").
    - **failure_response**: What you will say if the player fails (e.g., "I don't think so. Move along.").

IMPORTANT: You MUST output valid JSON. If you are offering a quest, the 'quest_offered' field is mandatory. If resolving a quest, 'quest_resolved' is mandatory. Do not just write the dialogue.
""".replace("{ANTI_YES_AND_RULES}", ANTI_YES_AND_RULES).replace("{ENTITY_TIER_DESCRIPTIONS}", ENTITY_TIER_DESCRIPTIONS).replace("{QUEST_REWARD_RULES}", QUEST_REWARD_RULES)

def validate_quest_output(quest_data: Dict[str, Any]) -> List[str]:
    """Returns list of validation errors, empty if valid."""
    errors = []
    
    if not quest_data:
        return ["Quest data is empty or None"]
    
    # Check for required fields in Quest
    required_quest = ["id", "description", "challenges", "accept_response", "refuse_response"]
    for field in required_quest:
        if field not in quest_data:
            errors.append(f"Missing required field in quest: {field}")
            
    if "challenges" in quest_data:
        for challenge in quest_data["challenges"]:
            # Check difficulty matches DC
            dc_map = {"easy": 10, "medium": 15, "hard": 20, "heroic": 25}
            difficulty = challenge.get("difficulty")
            dc = challenge.get("dc")
            
            if difficulty and dc and dc != dc_map.get(difficulty):
                errors.append(f"DC {dc} doesn't match difficulty level {difficulty}")
            
            # Check challenge type is valid
            valid_types = ["strength", "dexterity", "intelligence", "charisma"]
            if challenge.get("type") not in valid_types:
                errors.append(f"Invalid challenge type: {challenge.get('type')}")
            
            # Check for required fields in Challenge
            required_challenge = ["id", "type", "dc", "description"]
            for field in required_challenge:
                if field not in challenge:
                    errors.append(f"Missing required field in challenge: {field}")
    
    return errors