from typing import List, Dict, Tuple, Any, Optional
import logging
import json
import random
import re

from .models import GameWorldData, TurnResponse
from .llm_provider import generate_llm_response
from .world_tools import (
    MAX_TOOL_CALLS_PER_TURN,
    TOOLS_BLOCK,
    execute_tool_call,
    parse_tool_calls,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# NOTE: GEMINI_API_KEY is no longer required at import time. The Gemini client
# is created lazily by llm_provider.GeminiProvider when a call actually needs it,
# so the game can run fully on a local LLM (CHATRPG_PROVIDER=local).


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
    """Provider-agnostic turn generation (kept name for call-site compatibility)."""
    try:
        text = await generate_llm_response(conversation_history,
                                           system_instruction=system_instruction)
        dialogue, metadata = extract_json_metadata(text)
        return dialogue, metadata
    except Exception as e:
        logger.error(
            f"Error calling LLM for text generation: "
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

        history = [{"role": "user", "parts": [prompt]}]
        response_text = await generate_llm_response(history, json_mode=True)

        # Extract and return the JSON data
        _, metadata = extract_json_metadata(response_text)
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
        response_text = await generate_llm_response(
            [{"role": "user", "parts": [prompt.strip()]}])
        return response_text.strip()
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
        response_text = await generate_llm_response(
            [{"role": "user", "parts": [prompt.strip()]}])
        return response_text.strip()
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
        response_text = await generate_llm_response(
            [{"role": "user", "parts": [prompt.strip()]}], json_mode=True)
        
        _, metadata = extract_json_metadata(response_text)
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

# The single difficulty table (slice 1). The prompt, the validator, and the
# resolver all read from here — there was one table too many before this:
# entity tiers with DC ranges, value tiers with the same ranges under
# different names, and easy/medium/hard/heroic with fixed DCs that
# disagreed with both (DC 15 was "medium" in one vocabulary and "tough"
# in another). One table now. Labels map onto tier bands; trivial and
# legendary are tier-only, with no label.
DIFFICULTY_TABLE = {
    "trivial":   {"dc_range": (5, 8),   "label": None,       "examples": "Rats, drunk peasants, children. No real threat."},
    "average":   {"dc_range": (10, 12), "label": "easy",     "examples": "Town guards, wild animals, common bandits. Fair fight."},
    "tough":     {"dc_range": (15, 17), "label": "medium",   "examples": "Veteran soldiers, dire wolves, skilled duelists. Challenging."},
    "elite":     {"dc_range": (18, 20), "label": "hard",     "examples": "Champion fighters, young dragons, master assassins. Requires prep."},
    "boss":      {"dc_range": (22, 25), "label": "heroic",   "examples": "Warlords, ancient dragons, demon lords. Major climax."},
    "legendary": {"dc_range": (26, 30), "label": None,       "examples": "Gods, apocalyptic threats. Campaign defining."},
}
DIFFICULTY_LABEL_TO_TIER = {
    entry["label"]: tier
    for tier, entry in DIFFICULTY_TABLE.items()
    if entry["label"] is not None
}
CHALLENGE_TYPES = ["strength", "dexterity", "intelligence", "charisma"]


def assign_dc(tier: Optional[str] = None, difficulty: Optional[str] = None,
              modifiers: Optional[List[int]] = None) -> int:
    """The engine owns the DC. The model proposes a tier or a difficulty
    label; the engine rolls inside the tier's band and applies integer
    modifiers (knowledge, relationship, approach — summed). The model
    never writes a DC."""
    if tier is None and difficulty is not None:
        tier = DIFFICULTY_LABEL_TO_TIER.get(difficulty)
    if tier is None or tier not in DIFFICULTY_TABLE:
        tier = "average"
    lo, hi = DIFFICULTY_TABLE[tier]["dc_range"]
    dc = random.randint(lo, hi)
    if modifiers:
        dc += sum(modifiers)
    return max(1, dc)


def _tier_descriptions() -> str:
    lines = ["ENTITY TIERS (Use these to gauge difficulty):"]
    for tier, entry in DIFFICULTY_TABLE.items():
        lo, hi = entry["dc_range"]
        label = f" (label: {entry['label']})" if entry["label"] else ""
        lines.append(f"- {tier.capitalize()}: {entry['examples']} (DC {lo}-{hi}){label}")
    return "\n".join(lines) + "\n"


ENTITY_TIER_DESCRIPTIONS = _tier_descriptions()

QUEST_REWARD_RULES = """
QUEST REWARD SYSTEM:

NPC RESOURCE PROFILE:
- Resource Level: {resource_level}
- Can Offer: {can_offer}
- Cannot Offer: {cannot_offer}

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

When offering a quest, put "rewards" inside "quest_offered" in the single
turn JSON, as an object with "gold" (int) and "items" (list of strings).
When the player accepts a quest, set "quest_accepted" to the quest id.
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

WHERE YOU ARE — WHAT IS TRUE HERE (the engine's bindings, not your memory):
{bindings}

Use these bindings when you talk about the world. The map, the exits, and who is standing where are facts the engine holds; your job is to speak from them, not to re-derive them. If the player asks about a place or a person that is not in these bindings, say what you know honestly — or say you do not know — and list it in "referrals" or "new_entities" in your turn JSON rather than inventing details in passing.

WHAT YOU CAN ASK OF THIS PLAYER (rollable checks):
{rollable_band}
A check only gets rolled if its DC falls inside the band for that stat; below the band it succeeds on its own, above it it cannot be attempted. Propose checks inside the band.

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
When the player asks for a quest or you determine the NPC has a task, put it in the turn JSON (see THE TURN below).

1. "challenges" is a list of steps.
2. "tier" MUST be one of: trivial, average, tough, elite, boss, legendary. Give a tier per challenge. NEVER write a "dc" — the engine assigns the DC (a random roll inside the tier's band). A challenge you write without a DC is correct; a challenge with one will have it overwritten.
3. Choose the challenge type by the nature of the step:
   - strength: Physical force, combat, breaking things
   - dexterity: Stealth, agility, precision
   - intelligence: Puzzles, knowledge, investigation
   - charisma: Persuasion, deception, social interaction
4. ONLY include entities that are:
   - Already established in the world
   - OR being introduced by this quest (mark in involved_entities)

Example (note the lowercase type, the tier, and no "dc" anywhere):

{{
    "quest_offered": {{
        "id": "quest_id_snake_case",
        "description": "Brief description of the task.",
        "giver_npc": "Your name",
        "tier": "average",
        "accept_response": "Great, I knew I could count on you.",
        "refuse_response": "A pity. I'll find someone else.",
        "challenges": [
            {{
                "id": "challenge_id_snake_case",
                "type": "strength",
                "tier": "average",
                "description": "Lift the heavy beam."
            }}
        ],
        "involved_entities": ["Entity Name 1", "Entity Name 2"]
    }}
}}

THE TURN — YOUR WHOLE REPLY IS ONE JSON OBJECT:
{{
    "dialogue": "What you say to the player, in character.",
    "quest_offered": {{ ... }} (only when offering a quest, as above),
    "quest_accepted": "quest_id" (only when the player accepts a quest),
    "quest_resolved": "quest_id" (only when the player explicitly reports completing a quest to you),
    "skill_check": {{ (only when the conversation calls for an immediate check)
        "type": "charisma",
        "difficulty": "medium",
        "description": "Persuade the guard to let you pass.",
        "success_response": "Very well, you make a compelling point.",
        "failure_response": "I don't think so. Move along."
    }},
    "new_entities": [ {{ "kind": "character", "name": "Joren", "description": "A trapper who has seen the man.", "location": "Oakhaven" }} ] (only for people, places, or things you invented that are not in the world yet; "kind" is one of "character", "location", "item", "other" — use "other" for anything that is not clearly one of the first three, such as a plant or a substance),
    "referrals": [ {{ "to_name": "Joren", "to_kind": "person", "reason": "He has seen the man." }} ] (only when you send the player to someone more knowledgeable; "to_kind" is "person" or "place"),
    "claims": [ {{ "text": "Brom works at the smithy in Oakhaven.", "kind": "fact", "subject": "Brom" }} ] (every factual claim you made in your dialogue, each marked "fact", "inference", or "rumor"),
    "memory_update": ["The player promised to help with the moonpetal."] (only short facts worth remembering about this player)
}}

Rules for the turn JSON:
- "dialogue" is always present. Every other field is optional — include it only when it applies.
- For "skill_check": give a difficulty label only — "easy", "medium", "hard", or "heroic" (easy = average band, medium = tough, hard = elite, heroic = boss). NEVER write a "dc" — the engine computes it.
- For "claims": list what you asserted in your dialogue. Mark it "fact" if you stated it as true, "inference" if you reasoned it from what you know, "rumor" if you heard it secondhand. If you are not sure of something, say so in character and mark it "rumor". Name only places and people from your bindings above or a tool result this turn; if you named anything else, list it in "claims" anyway — the engine checks every claim against the world.
- For "referrals": if you do not know something and you know who would, send the player there instead of guessing.
- For "new_entities": only things you invented in this turn. Never list anything already in the world. "kind" must be one of "character", "location", "item", "other".
- For "quest_resolved": ONLY when the player explicitly reports completion to you.

CURRENT PLAYER STATS:
{player_stats}

CURRENT NPC STATE:
{npc_state}

IMPORTANT: Do not invent new attributes for existing entities.
Stick to established facts from the world data. Name only places and people that are in the bindings above, that you were told about in this conversation, or that you list in "new_entities" — never invent a place or a person in passing and leave it unlisted.

IMPORTANT: Your whole reply MUST be one valid JSON object in the shape above, with "dialogue" always present. If you are offering a quest, the 'quest_offered' field is mandatory. If resolving a quest, 'quest_resolved' is mandatory. Do not just write the dialogue.
""".replace("{ANTI_YES_AND_RULES}", ANTI_YES_AND_RULES).replace("{ENTITY_TIER_DESCRIPTIONS}", ENTITY_TIER_DESCRIPTIONS).replace("{QUEST_REWARD_RULES}", QUEST_REWARD_RULES)

def validate_quest_output(quest_data: Dict[str, Any]) -> List[str]:
    """Returns list of validation errors, empty if valid.

    Slice 1 contract: the model proposes a tier and/or a difficulty label
    per challenge and NEVER writes a `dc` — the engine assigns it. So `dc`
    is not required here (and a supplied one is ignored downstream, not an
    error, since older prompts may still emit it). What is an error: an
    unknown type, tier, or label, or a tier and a label that disagree.
    """
    errors = []

    if not quest_data:
        return ["Quest data is empty or None"]

    # Check for required fields in Quest
    required_quest = ["id", "description", "challenges", "accept_response", "refuse_response"]
    for field in required_quest:
        if field not in quest_data:
            errors.append(f"Missing required field in quest: {field}")

    if "tier" in quest_data and quest_data["tier"] not in DIFFICULTY_TABLE:
        errors.append(f"Invalid quest tier: {quest_data['tier']}")

    if "challenges" in quest_data:
        for challenge in quest_data["challenges"]:
            # Check challenge type is valid (lowercase, as the prompt shows)
            if challenge.get("type") not in CHALLENGE_TYPES:
                errors.append(f"Invalid challenge type: {challenge.get('type')}")

            # Tier and difficulty label must each be known, and agree
            tier = challenge.get("tier")
            difficulty = challenge.get("difficulty")
            if tier is not None and tier not in DIFFICULTY_TABLE:
                errors.append(f"Invalid challenge tier: {tier}")
            if difficulty is not None and difficulty not in DIFFICULTY_LABEL_TO_TIER:
                errors.append(f"Invalid challenge difficulty: {difficulty}")
            if tier is not None and difficulty is not None:
                if DIFFICULTY_LABEL_TO_TIER[difficulty] != tier:
                    errors.append(
                        f"Challenge tier {tier} disagrees with difficulty "
                        f"{difficulty} (maps to {DIFFICULTY_LABEL_TO_TIER[difficulty]})"
                    )

            # A supplied dc must at least be an integer; the engine
            # overwrites it with its own assignment either way.
            dc = challenge.get("dc")
            if dc is not None and not isinstance(dc, int):
                errors.append(f"Challenge dc must be an integer, got: {dc!r}")

            # Check for required fields in Challenge (dc and quest_id are
            # engine-supplied, so they are not required from the model)
            required_challenge = ["id", "type", "description"]
            for field in required_challenge:
                if field not in challenge:
                    errors.append(f"Missing required field in challenge: {field}")

    return errors


def validate_skill_check_proposal(skill_check: Dict[str, Any]) -> List[str]:
    """Validate an immediate skill check from a turn. Label only — the
    engine computes the DC, so `dc` is neither required nor checked."""
    errors = []
    if not skill_check:
        return ["Skill check is empty or None"]
    if skill_check.get("type") not in CHALLENGE_TYPES:
        errors.append(f"Invalid skill check type: {skill_check.get('type')}")
    if skill_check.get("difficulty") not in DIFFICULTY_LABEL_TO_TIER:
        errors.append(f"Invalid skill check difficulty: {skill_check.get('difficulty')}")
    for field in ["description", "success_response", "failure_response"]:
        if field not in skill_check:
            errors.append(f"Missing required field in skill check: {field}")
    return errors


async def get_structured_turn(conversation_history: List[Dict],
                              system_instruction: str = None) -> TurnResponse:
    """One conversation turn, structured. Requests the reply in JSON mode
    and Pydantic-validates it into a TurnResponse, replacing the regex
    extraction for conversation. On any failure (bad JSON, failed
    validation, provider error) it falls back to whatever dialogue could
    be extracted, with everything else empty — the turn still plays,
    just without structured effects."""
    text = ""
    try:
        text = await generate_llm_response(conversation_history,
                                           system_instruction=system_instruction,
                                           json_mode=True)
        _, metadata = extract_json_metadata(text)
        return TurnResponse.model_validate(metadata)
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning(f"Structured turn failed validation, falling back to dialogue only: {e}")
        try:
            dialogue, _ = extract_json_metadata(text)
        except Exception:
            dialogue = "I'm sorry, I seem to be having trouble responding right now."
        return TurnResponse(dialogue=dialogue)
    except Exception as e:
        logger.error(
            f"Error calling LLM for structured turn: {type(e).__name__}: {e}"
        )
        return TurnResponse(dialogue="I'm sorry, I seem to be having trouble responding right now.")


async def get_structured_turn_with_tools(
        conversation_history: List[Dict],
        system_instruction: str = None,
        session_id: str = None,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN
) -> Tuple[TurnResponse, List[Dict[str, Any]]]:
    """One conversation turn with the tool-call loop (slice 2).

    The model gets the usual system instruction plus the tools block.
    When its reply is a tool-calls block, the engine executes the calls
    against the loaded world (cap `max_tool_calls` per turn), feeds the
    JSON results back as a user-role message, and asks again. When the
    reply is the final TurnResponse — or the budget is spent — the turn
    is parsed and validated exactly like `get_structured_turn`, with
    the same dialogue-only fallback.

    Returns the TurnResponse and the list of tool calls made this turn
    (each with its tool, args, and result), so the caller — and the
    validation-invariant slice after this one — can check what the
    model grounded its claims on. The tool exchanges stay turn-local:
    the caller's conversation history is not mutated with them.

    Textual tool calls, not native function calling, on purpose: the
    prototype (§18) validated this exact format, and it works
    identically across the Gemini and local providers without depending
    on either one's function-calling support.
    """
    history = list(conversation_history)
    system = (system_instruction or "") + TOOLS_BLOCK
    tool_calls_made: List[Dict[str, Any]] = []
    calls_used = 0
    text = ""
    try:
        while True:
            text = await generate_llm_response(
                history, system_instruction=system, json_mode=True)
            calls = parse_tool_calls(text)
            if not calls:
                break
            if calls_used >= max_tool_calls:
                # Budget spent and the model still wants a tool: tell it
                # once, plainly, that it must answer now, and ask again.
                logger.info("Tool budget spent; forcing the final TurnResponse.")
                history = history + [{
                    "role": "user",
                    "parts": ["[System] You have used all your tool calls "
                              "for this turn. Answer now with your final "
                              "TurnResponse JSON and no more tool calls."],
                }]
                text = await generate_llm_response(
                    history, system_instruction=system, json_mode=True)
                break
            batch = calls[:max_tool_calls - calls_used]
            results = []
            for call in batch:
                result = execute_tool_call(session_id, call.get("tool"),
                                           call.get("args") or {})
                record = {"tool": call.get("tool"),
                          "args": call.get("args") or {},
                          "result": result}
                results.append(record)
                tool_calls_made.append(record)
                calls_used += 1
                logger.info(f"Tool call: {record['tool']} "
                            f"{record['args']} -> {result}")
            history = history + [{
                "role": "user",
                "parts": ["[Tool results]\n"
                          f"{json.dumps(results, indent=2)}\n"
                          "Use these results to answer. Reply with your "
                          "final TurnResponse JSON, or with at most one "
                          "more tool call if you still need a fact."],
            }]
        _, metadata = extract_json_metadata(text)
        return TurnResponse.model_validate(metadata), tool_calls_made
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning(f"Structured turn with tools failed validation, "
                       f"falling back to dialogue only: {e}")
        try:
            dialogue, _ = extract_json_metadata(text)
        except Exception:
            dialogue = "I'm sorry, I seem to be having trouble responding right now."
        return TurnResponse(dialogue=dialogue), tool_calls_made
    except Exception as e:
        logger.error(
            f"Error calling LLM for structured turn with tools: {type(e).__name__}: {e}"
        )
        return TurnResponse(dialogue="I'm sorry, I seem to be having trouble responding right now."), tool_calls_made