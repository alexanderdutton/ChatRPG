import sqlite3
import json
import os
from typing import Dict, List, Any, Optional
from .game_world import game_world
from .gemini_service import generate_item_details, generate_quest
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "game_data.db")

DEFAULT_NPC_STATE = {
    "mood": "content",
    "relationship": 50,
    "quests_given_recently": 0,
    "last_quest_given": 0,
    "quest_cooldown_expires": 0
}

RELATIONSHIP_MODIFIERS = {
    "quest_accepted": 2,
    "auto_success": 5,
    "roll_success": 8,
    "minor_failure": -2,
    "major_failure": -5,
    "severe_failure": -10,
    "critical_failure": -15,
    "quest_refused": -3,
    "quest_abandoned": -8
}

ENTITY_TIERS = {
    "trivial": {
        "dc_range": (5, 8),
        "hp_range": (1, 5),
        "damage": "1d4",
        "description": "Rats, drunk peasants, children. No real threat.",
        "examples": ["Rat", "Drunkard", "Child"]
    },
    "average": {
        "dc_range": (10, 12),
        "hp_range": (10, 20),
        "damage": "1d6",
        "description": "Town guards, wild animals, common bandits. Fair fight.",
        "examples": ["Guard", "Wolf", "Bandit"]
    },
    "tough": {
        "dc_range": (15, 17),
        "hp_range": (25, 40),
        "damage": "2d6",
        "description": "Veteran soldiers, dire wolves, skilled duelists. Challenging.",
        "examples": ["Veteran", "Dire Wolf", "Duelist"]
    },
    "elite": {
        "dc_range": (18, 20),
        "hp_range": (50, 80),
        "damage": "3d6",
        "description": "Champion fighters, young dragons, master assassins. Requires prep.",
        "examples": ["Champion", "Young Dragon", "Assassin"]
    },
    "boss": {
        "dc_range": (22, 25),
        "hp_range": (100, 150),
        "damage": "4d6+5",
        "description": "Warlords, ancient dragons, demon lords. Major climax.",
        "examples": ["Warlord", "Ancient Dragon", "Demon Lord"]
    },
    "legendary": {
        "dc_range": (26, 30),
        "hp_range": (200, 500),
        "damage": "6d8+10",
        "description": "Gods, apocalyptic threats. Campaign defining.",
        "examples": ["God", "Titan", "Avatar"]
    }
}

VALUE_TIERS = {
    "tier_0_trivial": {
        "xp": (5, 15),
        "base_reward": "goodwill",
        "difficulty_dc": (5, 8),
        "gold": (1, 5), # Kept for backward compatibility/reference
        "items": ["rusty_dagger", "stale_bread", "pebble"]
    },
    "tier_1_minor": {
        "xp": (25, 50),
        "base_reward": "modest",
        "difficulty_dc": (10, 12),
        "gold": (10, 25),
        "items": ["iron_sword", "leather_armor", "healing_potion"]
    },
    "tier_2_standard": {
        "xp": (100, 200),
        "base_reward": "fair",
        "difficulty_dc": (15, 17),
        "gold": (50, 100),
        "items": ["steel_sword", "chainmail", "quality_healing_potion"]
    },
    "tier_3_significant": {
        "xp": (300, 500),
        "base_reward": "generous",
        "difficulty_dc": (18, 20),
        "gold": (150, 300),
        "items": ["enchanted_blade", "plate_armor", "rare_elixir"]
    },
    "tier_4_major": {
        "xp": (750, 1500),
        "base_reward": "lavish",
        "difficulty_dc": (22, 25),
        "gold": (500, 1000),
        "items": ["legendary_weapon", "artifact_armor", "phoenix_down"]
    },
    "tier_5_legendary": {
        "xp": (2000, 5000),
        "base_reward": "legendary",
        "difficulty_dc": (26, 30),
        "gold": (2000, 5000),
        "items": ["god_killer", "world_shaper"]
    }
}

# Map old tier names to new ones for backward compatibility
TIER_MAPPING = {
    "trivial": "tier_0_trivial",
    "average": "tier_1_minor", 
    "tough": "tier_2_standard",
    "elite": "tier_3_significant", # or tier_4 depending on interpretation, let's map to 3
    "boss": "tier_4_major",
    "legendary": "tier_5_legendary"
}

NPC_RESOURCE_LEVELS = {
    "destitute": {
        "description": "Has nothing to give",
        "can_offer": ["gratitude", "information", "future_favor"],
        "cannot_offer": ["gold", "items", "property"],
        "examples": "beggars, refugees, children, imprisoned"
    },
    "poor": {
        "description": "Subsistence living",
        "can_offer": ["food", "simple_tools", "shelter", "minor_favor"],
        "gold_range": (1, 10),
        "item_tier": "common",
        "examples": "peasants, laborers, struggling merchants"
    },
    "modest": {
        "description": "Working class",
        "can_offer": ["modest_gold", "quality_goods", "trade_services"],
        "gold_range": (10, 50),
        "item_tier": "common_to_uncommon",
        "examples": "craftsmen, guards, innkeepers, small merchants"
    },
    "comfortable": {
        "description": "Established professionals",
        "can_offer": ["good_gold", "fine_items", "connections", "property_access"],
        "gold_range": (50, 200),
        "item_tier": "uncommon_to_rare",
        "examples": "successful merchants, minor nobles, guild masters"
    },
    "wealthy": {
        "description": "Local elite",
        "can_offer": ["substantial_gold", "rare_items", "political_favors", "land_grants"],
        "gold_range": (200, 1000),
        "item_tier": "rare_to_epic",
        "examples": "nobles, merchant princes, high priests"
    },
    "opulent": {
        "description": "Regional powers",
        "can_offer": ["vast_gold", "legendary_items", "titles", "armies"],
        "gold_range": (1000, 10000),
        "item_tier": "epic_to_legendary",
        "examples": "kings, archmages, dragons, gods"
    }
}

class GameStateManager:

    def __init__(self):
        self.init_db()
        print("GameStateManager initialized with SQLite.")

    def init_db(self):
        """Initializes the SQLite database and creates tables if they don't exist."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                player_name TEXT,
                current_location_name TEXT,
                player_x INTEGER,
                player_y INTEGER,
                inventory TEXT,
                health INTEGER,
                gold INTEGER,
                quest_log TEXT,
                npc_states TEXT,
                conversation_history TEXT,
                conversation_partner TEXT,
                game_mode TEXT
            )
        ''')
        # Attempt to add game_mode column if it doesn't exist (migration)
        try:
            cursor.execute("ALTER TABLE sessions ADD COLUMN game_mode TEXT DEFAULT 'EXPLORATION'")
        except sqlite3.OperationalError:
            pass # Column likely already exists

        # Attempt to add response columns to quests table (migration)
        try:
            cursor.execute("ALTER TABLE quests ADD COLUMN accept_response TEXT")
            cursor.execute("ALTER TABLE quests ADD COLUMN refuse_response TEXT")
        except sqlite3.OperationalError:
            pass # Columns likely already exist

        conn.commit()
        
        # New Tables for Challenge System
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_stats (
                session_id TEXT PRIMARY KEY,
                strength INTEGER DEFAULT 10,
                dexterity INTEGER DEFAULT 10,
                intelligence INTEGER DEFAULT 10,
                charisma INTEGER DEFAULT 10,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quests (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                giver_npc TEXT,
                description TEXT,
                status TEXT DEFAULT 'active',
                involved_entities TEXT,
                accept_response TEXT,
                refuse_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS challenges (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                quest_id TEXT,
                type TEXT,
                dc INTEGER,
                description TEXT,
                completed BOOLEAN DEFAULT FALSE,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entity_stubs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                name TEXT,
                type TEXT,
                description TEXT,
                related_to TEXT,
                status TEXT,
                needs_expansion BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')
        
        conn.commit()
        conn.close()

    def should_require_roll(self, challenge_dc: int, player_stat: int) -> Dict[str, Any]:
        """
        Determines if a roll is needed or if auto-success/failure applies.
        """
        margin = player_stat - challenge_dc
        
        if margin >= 5:
            return {
                "requires_roll": False,
                "outcome": "auto_success",
                "narrative": "Trivial task."
            }
        elif margin <= -10:
            return {
                "requires_roll": False,
                "outcome": "auto_failure",
                "narrative": "Beyond your ability."
            }
        else:
            return {
                "requires_roll": True,
                "outcome": "uncertain"
            }

    def calculate_failure_severity(self, roll_total: int, dc: int, is_crit_fail: bool = False) -> str:
        """
        Determines how badly the player failed.
        """
        if is_crit_fail:
            return "critical"
            
        margin = roll_total - dc
        
        if margin >= -3:
            return "minor"     # Close call (missed by 1-3)
        elif margin >= -8:
            return "major"     # Clear failure (missed by 4-8)
        else:
            return "severe"    # Catastrophic (missed by 9+)

    def update_relationship(self, session_id: str, npc_name: str, change_amount: int | str):
        """
        Updates the relationship score with an NPC.
        change_amount can be an integer or an event string (e.g., 'quest_accepted').
        """
        npc_state = self.get_npc_state(session_id, npc_name)
        
        modifier = 0
        if isinstance(change_amount, str):
            modifier = RELATIONSHIP_MODIFIERS.get(change_amount, 0)
        else:
            modifier = change_amount

        current_relationship = npc_state.get("relationship", 50)
        new_relationship = max(0, min(100, current_relationship + modifier))
        
        npc_state["relationship"] = new_relationship
        
        # TODO: Implement mood changes based on quest outcomes and relationship
        
        self.update_npc_state(session_id, npc_name, npc_state)
        logger.info(f"Updated relationship with {npc_name}: {current_relationship} -> {new_relationship} (Modifier: {modifier})")

    def get_tier_config(self, tier_name: str) -> Dict[str, Any]:
        """Retrieves configuration for a specific entity tier."""
        # Handle new tier names and backward compatibility
        mapped_tier = TIER_MAPPING.get(tier_name.lower(), tier_name.lower())
        return VALUE_TIERS.get(mapped_tier, VALUE_TIERS["tier_1_minor"])

    def calculate_quest_rewards(self, quest_tier: str, npc_profile: Dict[str, Any], relationship: int) -> Dict[str, Any]:
        """
        Determines appropriate rewards based on context.
        """
        # Map tier if needed
        mapped_tier = TIER_MAPPING.get(quest_tier.lower(), quest_tier.lower())
        tier_data = VALUE_TIERS.get(mapped_tier, VALUE_TIERS["tier_1_minor"])
        
        resource_level_key = npc_profile.get("resource_level", "poor")
        resource_level = NPC_RESOURCE_LEVELS.get(resource_level_key, NPC_RESOURCE_LEVELS["poor"])
        
        # XP is always given (universal)
        base_xp = tier_data["xp"]
        import random
        xp = random.randint(base_xp[0], base_xp[1])
        
        # Relationship modifier (better relationship = slightly better rewards)
        relationship_multiplier = 1.0 + (relationship - 50) / 200  # 0.75x to 1.25x
        
        rewards = {
            "xp": int(xp * relationship_multiplier),
            "material_rewards": []
        }
        
        # Determine if NPC can offer material rewards
        if "gold" in resource_level["can_offer"] or "gold_range" in resource_level:
            gold_range = resource_level.get("gold_range", (0, 0))
            
            # Scale gold by quest tier
            # We need a multiplier based on the tier index or relative value
            # Let's derive it from the tier's gold vs base tier gold
            # Or use the multiplier logic from the user request
            tier_multipliers = {
                "tier_0_trivial": 0.5,
                "tier_1_minor": 1.0,
                "tier_2_standard": 2.0,
                "tier_3_significant": 4.0,
                "tier_4_major": 8.0,
                "tier_5_legendary": 16.0
            }
            tier_multiplier = tier_multipliers.get(mapped_tier, 1.0)
            
            min_gold = int(gold_range[0] * tier_multiplier * relationship_multiplier)
            max_gold = int(gold_range[1] * tier_multiplier * relationship_multiplier)
            
            if max_gold > 0:
                rewards["material_rewards"].append({
                    "type": "gold",
                    "amount": random.randint(min_gold, max_gold)
                })
        
        # Check for other rewards NPC can offer
        if "items" in resource_level["can_offer"]:
            item_tier = resource_level.get("item_tier", "common")
            rewards["material_rewards"].append({
                "type": "item",
                "tier": item_tier,
                "source": npc_profile.get("name", "Unknown")
            })
        
        if "information" in resource_level["can_offer"]:
            rewards["material_rewards"].append({
                "type": "information",
                "value": "lore_or_quest_hook"
            })
        
        if "future_favor" in resource_level["can_offer"]:
            rewards["material_rewards"].append({
                "type": "favor_token",
                "redeemable_with": npc_profile.get("name", "Unknown") # Use name or ID if available
            })
        
        return rewards

    def validate_quest_rewards(self, quest_data: Dict[str, Any]) -> Dict[str, Any]:
        """Ensures quest rewards match the quest tier."""
        tier_raw = quest_data.get("tier", "average").lower()
        tier = TIER_MAPPING.get(tier_raw, "tier_1_minor")
        tier_rules = VALUE_TIERS.get(tier, VALUE_TIERS["tier_1_minor"])
        
        # Validate Gold
        gold_reward = quest_data.get("rewards", {}).get("gold", 0)
        max_gold = tier_rules["gold"][1]
        if gold_reward > max_gold:
            logger.warning(f"Capping gold reward for {tier} quest from {gold_reward} to {max_gold}")
            if "rewards" not in quest_data:
                quest_data["rewards"] = {}
            quest_data["rewards"]["gold"] = max_gold
            
        # Validate Items (Basic check: ensure items exist in game world or are generic)
        # For now, we trust the LLM on item names but could restrict to the tier list
        # strict_items = tier_rules["items"]
        # item_rewards = quest_data.get("rewards", {}).get("items", [])
        # filtered_items = [i for i in item_rewards if i in strict_items]
        # if len(filtered_items) != len(item_rewards):
        #     logger.warning(f"Filtered invalid items for {tier} quest.")
        #     quest_data["rewards"]["items"] = filtered_items
            
        return quest_data

    def _get_session_data(self, session_id: str) -> Dict[str, Any] | None:
        """Helper to retrieve full session data."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            data = dict(row)
            # Deserialize JSON fields
            for field in ['inventory', 'quest_log', 'npc_states', 'conversation_history']:
                if data[field]:
                    try:
                        data[field] = json.loads(data[field])
                    except json.JSONDecodeError:
                        data[field] = [] if field != 'npc_states' else {}
            return data
        return None

    def _update_session_field(self, session_id: str, field: str, value: Any):
        """Helper to update a single field in the session."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if field in ['inventory', 'quest_log', 'npc_states', 'conversation_history']:
            value = json.dumps(value)
            
        cursor.execute(f"UPDATE sessions SET {field} = ? WHERE session_id = ?", (value, session_id))
        conn.commit()
        conn.close()

    def create_session(self, session_id: str, player_name: str = "Traveler"):
        """Creates a new game session."""
        if self.session_exists(session_id):
            return

        # Initialize NPC states
        npc_states = {}
        for location_name, characters_in_location in game_world.characters.items():
            for character_name, character_obj in characters_in_location.items():
                npc_states[character_name] = {
                    'location': location_name,
                    'x': character_obj.x,
                    'y': character_obj.y
                }

        # Get the first location from the loaded world
        first_location_name = list(game_world.locations.keys())[0]
        first_location = game_world.get_location(first_location_name)

        player_start_x = 0
        player_start_y = 0
        if first_location and first_location.player_initial_location:
            player_start_x = first_location.player_initial_location.get("x", 0)
            player_start_y = first_location.player_initial_location.get("y", 0)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (
                session_id, player_name, current_location_name, player_x, player_y,
                inventory, health, gold, quest_log, npc_states, conversation_history, conversation_partner, game_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, player_name, first_location_name, player_start_x, player_start_y,
            json.dumps([]), 100, 0, json.dumps([]), json.dumps(npc_states), json.dumps([]), None, "EXPLORATION"
        ))
        conn.commit()

        # Initialize Player Stats
        cursor.execute('INSERT INTO player_stats (session_id) VALUES (?)', (session_id,))
        conn.commit()
        
        conn.close()
        print(f"Session {session_id} created with initial NPC states.")

    def session_exists(self, session_id: str) -> bool:
        """Checks if a session exists."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def get_conversation_partner(self, session_id: str) -> Optional[str]:
        """Retrieves the current conversation partner ID."""
        data = self._get_session_data(session_id)
        partner = data.get("conversation_partner")
        # print(f"DEBUG: get_conversation_partner for {session_id} -> {partner}") # Commented out to reduce noise
        return partner

    def get_conversation_history(self, session_id: str) -> List[Dict]:
        """Retrieves the conversation history for a given session."""
        data = self._get_session_data(session_id)
        return data.get("conversation_history", []) if data else []

    def update_conversation_history(self, session_id: str, history: List[Dict]):
        """Updates the conversation history for a given session."""
        self._update_session_field(session_id, "conversation_history", history)

    def get_player_name(self, session_id: str) -> str:
        """Retrieves the player's name for a given session."""
        data = self._get_session_data(session_id)
        return data.get("player_name", "Traveler") if data else "Traveler"

    def set_player_name(self, session_id: str, name: str):
        """Sets the player's name for a given session."""
        self._update_session_field(session_id, "player_name", name)

    def get_current_location_name(self, session_id: str) -> str:
        """Retrieves the player's current location name for a given session."""
        data = self._get_session_data(session_id)
        return data.get("current_location_name", "Unknown Location") if data else "Unknown Location"

    def get_world_name(self) -> str:
        """Retrieves the current world name."""
        return game_world.name

    def set_current_location_name(self, session_id: str, location_name: str):
        """Sets the player's current location name for a given session."""
        self._update_session_field(session_id, "current_location_name", location_name)
        
        # Update player's x and y to the initial location of the new room
        new_location = game_world.get_location(location_name)
        if new_location and new_location.player_initial_location:
            self._update_session_field(session_id, "player_x", new_location.player_initial_location["x"])
            self._update_session_field(session_id, "player_y", new_location.player_initial_location["y"])

    def get_current_location_description(self, session_id: str) -> Dict[str, str]:
        """Retrieves the description components of the player's current location."""
        session = self._get_session_data(session_id)
        if not session:
            return {"error": "Session not found."}

        location_name = session.get("current_location_name")
        location = game_world.get_location(location_name)
        if not location:
            return {"error": "You are in an unknown location."}

        description_parts = {
            "location_description": location.description,
            "current_feature_description": "",
            "adjacent_features_description": "",
            "npcs_description": ""
        }

        # Add feature description if player is on a feature
        player_x = session["player_x"]
        player_y = session["player_y"]
        current_cell_char = location.raw_layout[player_y][player_x]
        for feature in location.features:
            if feature.name == location.map_key.get(current_cell_char):
                description_parts["current_feature_description"] = f"Located here is {feature.description}"
                break

        # Add descriptions of notable features in adjacent squares
        adjacent_features_list = []
        structured_adjacent_features = []
        directions = {
            "North": (0, -1), "South": (0, 1), "East": (1, 0), "West": (-1, 0),
            "North-East": (1, -1), "North-West": (-1, -1), "South-East": (1, 1), "South-West": (-1, 1)
        }
        for direction, (dx, dy) in directions.items():
            adj_x, adj_y = player_x + dx, player_y + dy
            if 0 <= adj_y < len(location.raw_layout) and 0 <= adj_x < len(location.raw_layout[0]):
                adj_cell_char = location.raw_layout[adj_y][adj_x]
                for feature in location.features:
                    if feature.name == location.map_key.get(adj_cell_char):
                        adjacent_features_list.append(f"To the {direction} there is {feature.name}.")
                        structured_adjacent_features.append({"name": feature.name, "direction": direction})
                        break
        if adjacent_features_list:
            description_parts["adjacent_features_description"] = " ".join(adjacent_features_list)
        
        description_parts["adjacent_features"] = structured_adjacent_features

        # Add NPC descriptions
        npcs_in_location = self.get_npcs_in_location(session_id)
        if npcs_in_location:
            npc_descriptions = []
            for npc in npcs_in_location:
                # Use coordinates directly from get_npcs_in_location result
                if npc["x"] == player_x and npc["y"] == player_y:
                    npc_descriptions.append(f"{npc['name']}: {npc['short_description']}")
            if npc_descriptions:
                description_parts["npcs_description"] = f"You see:\n- {', '.join(npc_descriptions)}"

        return description_parts
    
    def get_inventory(self, session_id: str) -> List[str]:
        """Retrieves the player's inventory for a given session."""
        data = self._get_session_data(session_id)
        return data.get("inventory", []) if data else []

    def add_item_to_inventory(self, session_id: str, item: str):
        """Adds an item to the player's inventory for a given session."""
        inventory = self.get_inventory(session_id)
        inventory.append(item)
        self._update_session_field(session_id, "inventory", inventory)

    def remove_item_from_inventory(self, session_id: str, item: str):
        """Removes an item from the player's inventory for a given session."""
        inventory = self.get_inventory(session_id)
        if item in inventory:
            inventory.remove(item)
            self._update_session_field(session_id, "inventory", inventory)

    def get_health(self, session_id: str) -> int:
        """Retrieves the player's health for a given session."""
        data = self._get_session_data(session_id)
        return data.get("health", 100) if data else 100

    def set_health(self, session_id: str, health: int):
        """Sets the player's health for a given session."""
        self._update_session_field(session_id, "health", health)

    def get_gold(self, session_id: str) -> int:
        """Retrieves the player's gold for a given session."""
        data = self._get_session_data(session_id)
        return data.get("gold", 0) if data else 0

    def set_gold(self, session_id: str, gold: int):
        """Sets the player's gold for a given session."""
        self._update_session_field(session_id, "gold", gold)

    def move_player(self, session_id: str, direction: str) -> str:
        session = self._get_session_data(session_id)
        if not session:
            return "Session not found."

        current_location_name = session.get("current_location_name")
        location = game_world.get_location(current_location_name)

        if not location or not location.raw_layout:
            return "You are in an unknown location or there is no map to move within."

        player_x = session["player_x"]
        player_y = session["player_y"]

        new_x, new_y = player_x, player_y

        if direction == "north":
            new_y -= 1
        elif direction == "south":
            new_y += 1
        elif direction == "east":
            new_x += 1
        elif direction == "west":
            new_x -= 1
        else:
            return "Invalid direction."

        # Check boundaries
        if not (0 <= new_y < len(location.raw_layout) and 
                0 <= new_x < len(location.raw_layout[0])):
            return "You cannot move in that direction. You would fall off the map!"

        # Check for collisions with impassable elements (walls)
        target_cell = location.raw_layout[new_y][new_x]
        if target_cell in location.map_key and location.map_key[target_cell] == "Wall":
            return "You hit a wall!"

        self._update_session_field(session_id, "player_x", new_x)
        self._update_session_field(session_id, "player_y", new_y)
        
        # Refresh session data after update
        session = self._get_session_data(session_id)

        response_message = f"You move {direction}."

        # Get current feature description
        current_cell_char = location.raw_layout[new_y][new_x]
        for feature in location.features:
            if feature.name == location.map_key.get(current_cell_char):
                response_message += f"\nYou are standing on: {feature.description}"
                break

        # Get adjacent feature names
        adjacent_features_list = []
        directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        for dir_name, (dx, dy) in directions.items():
            adj_x, adj_y = new_x + dx, new_y + dy
            if 0 <= adj_y < len(location.raw_layout) and 0 <= adj_x < len(location.raw_layout[0]):
                adj_cell_char = location.raw_layout[adj_y][adj_x]
                for feature in location.features:
                    if feature.name == location.map_key.get(adj_cell_char):
                        adjacent_features_list.append(f"To the {dir_name} is {feature.name}.")
                        break
        if adjacent_features_list:
            response_message += "\n" + " ".join(adjacent_features_list)

        # Get NPCs in current square
        npcs_in_location = self.get_npcs_in_location(session_id)
        if npcs_in_location:
            npc_descriptions = []
            for npc in npcs_in_location:
                npc_state = session["npc_states"][npc["id"]]
                if npc_state["x"] == new_x and npc_state["y"] == new_y:
                    npc_descriptions.append(f"{npc['name']}: {npc['short_description']}")
            if npc_descriptions:
                response_message += f"\nYou see:\n- {', '.join(npc_descriptions)}"

        return response_message

    def enter_exit(self, session_id: str) -> str:
        session = self._get_session_data(session_id)
        if not session:
            return "Session not found."

        current_location_name = session.get("current_location_name")
        location = game_world.get_location(current_location_name)

        if not location:
            return "You are in an unknown location."

        # Check if there's any exit from the current location
        if location.exits:
            # For simplicity, let's just pick the first available exit for now
            for direction, exit_location_name in location.exits.items():
                if exit_location_name:
                    new_location = game_world.get_location(exit_location_name)
                    if new_location:
                        self.set_current_location_name(session_id, exit_location_name)
                        return f"You enter {new_location.name}. {new_location.description}"
            return "There are no accessible exits from your current position."
        else:
            return "There are no exits from this location."

    def get_quest_log(self, session_id: str) -> List[str]:
        """Retrieves the player's quest log for a given session."""
        data = self._get_session_data(session_id)
        return data.get("quest_log", []) if data else []

    def add_quest_to_log(self, session_id: str, quest: str):
        """Adds a quest to the player's quest log for a given session."""
        quest_log = self.get_quest_log(session_id)
        quest_log.append(quest)
        self._update_session_field(session_id, "quest_log", quest_log)

    def remove_quest_from_log(self, session_id: str, quest: str):
        """Removes a quest from the player's quest log for a given session."""
        quest_log = self.get_quest_log(session_id)
        if quest in quest_log:
            quest_log.remove(quest)
            self._update_session_field(session_id, "quest_log", quest_log)

    async def process_command(self, session_id: str, command: str) -> str:
        """Processes a game command and returns a response."""
        command = command.lower().strip()
        session = self._get_session_data(session_id)
        if not session:
            return "Session ID is required for commands."

        # Check Game Mode
        game_mode = self.get_game_mode(session_id)
        
        if game_mode == "INTERACTION":
            if command in ["leave", "exit", "bye", "quit"]:
                print(f"DEBUG: Processing leave command. Current partner: {self.get_conversation_partner(session_id)}")
                self.end_interaction(session_id)
                return "You end the conversation."
            else:
                # In interaction mode, we might want to treat other commands as dialogue or just block them.
                # For now, let's block movement and standard commands, but maybe allow inventory?
                if command == "inventory":
                     inventory = self.get_inventory(session_id)
                     return f"Your inventory: {', '.join(inventory)}." if inventory else "Your inventory is empty."
                
                return "You are in a conversation. Type 'leave' to exit."

        # EXPLORATION MODE
        if command == "inventory":
            inventory = self.get_inventory(session_id)
            if inventory:
                return f"Your inventory: {', '.join(inventory)}."
            else:
                return "Your inventory is empty."
        elif command == "look":
            description_parts = self.get_current_location_description(session_id)
            response_message = description_parts["location_description"]
            if description_parts.get("current_feature_description"):
                response_message += f"\n{description_parts['current_feature_description']}"
            if description_parts.get("adjacent_features_description"):
                response_message += f"\n{description_parts['adjacent_features_description']}"
            if description_parts.get("npcs_description"):
                response_message += f"\n{description_parts['npcs_description']}"
            return response_message
        elif command.startswith("go "):
            direction = command[3:].strip()
            return self.move_player(session_id, direction)
        elif command.startswith("talk to "):
            npc_name = command[8:].strip()
            return self.initiate_dialogue(session_id, npc_name)
        elif command == "enter":
            return self.enter_exit(session_id)
        elif command.startswith("examine ") or command.startswith("look "):
            target_name = command.split(" ", 1)[1].strip()
            # Check inventory first
            inventory = self.get_inventory(session_id)
            found_item = next((i for i in inventory if target_name.lower() in i.lower()), None)
            if found_item:
                return await generate_item_details(found_item)
            
            # Check NPCs in location
            npcs = self.get_npcs_in_location(session_id)
            found_npc = next((n for n in npcs if target_name.lower() in n["name"].lower()), None)
            if found_npc:
                return f"{found_npc['name']}: {found_npc['short_description']}"
            
            return f"You don't see '{target_name}' here."
        elif command == "rumors":
            location_desc = self.get_current_location_description(session_id)
            context = f"Location: {location_desc['location_description']}. {location_desc['npcs_description']}"
            return await generate_quest(context)
        else:
            return "I don't understand that command."

    def get_npc_info(self, npc_id: str) -> Dict[str, Any]:
        character = game_world.get_character(npc_id)
        return character.dict() if character else None

    def get_npc_state(self, session_id: str, npc_name: str) -> Dict[str, Any]:
        """Retrieves the state of a specific NPC, initializing defaults if needed."""
        data = self._get_session_data(session_id)
        if not data:
            return DEFAULT_NPC_STATE.copy()
            
        npc_states = data.get("npc_states", {})
        state = npc_states.get(npc_name, {})
        
        # Merge with defaults
        full_state = DEFAULT_NPC_STATE.copy()
        full_state.update(state)
        
        # Decay quest counter
        self.decay_quest_counter(full_state)
        
        # If state changed (decay happened), update DB? 
        # Ideally yes, but for read-heavy ops maybe not every time. 
        # Let's update if decay happened.
        if full_state["quests_given_recently"] != state.get("quests_given_recently", 0):
             npc_states[npc_name] = full_state
             self._update_session_field(session_id, "npc_states", npc_states)
             
        return full_state

    def decay_quest_counter(self, npc_state: Dict[str, Any]):
        """Decays the quest spam counter over time."""
        import time
        if npc_state["quests_given_recently"] > 0:
            time_since = time.time() - npc_state.get("last_quest_given", 0)
            if time_since > 3600: # 1 hour
                decay_amount = int(time_since // 3600)
                npc_state["quests_given_recently"] = max(0, npc_state["quests_given_recently"] - decay_amount)

    def record_quest_given(self, session_id: str, npc_name: str):
        """Updates NPC state when a quest is given."""
        import time
        npc_state = self.get_npc_state(session_id, npc_name)
        npc_state["quests_given_recently"] += 1
        npc_state["last_quest_given"] = time.time()
        npc_state["quest_cooldown_expires"] = time.time() + 300 # 5 min cooldown? User didn't specify duration, assuming 5m
        
        self.update_npc_state(session_id, npc_name, npc_state)

    def update_npc_state(self, session_id: str, npc_name: str, updates: Dict[str, Any]):
        """Updates the state of a specific NPC."""
        data = self._get_session_data(session_id)
        if not data:
            return
        
        npc_states = data.get("npc_states", {})
        if npc_name not in npc_states:
            npc_states[npc_name] = {}
            
        npc_states[npc_name].update(updates)
        self._update_session_field(session_id, "npc_states", npc_states)

    def archive_conversation(self, session_id: str):
        """Archives the current conversation history and clears it."""
        # For now, we just clear it, assuming memory has been extracted.
        # In a full implementation, we might append it to a 'long_term_history' field.
        self._update_session_field(session_id, "conversation_history", [])

    def sync_world_npcs(self, session_id: str):
        """Syncs NPCs from game_world to the session if they are missing."""
        session = self._get_session_data(session_id)
        if not session:
            return

        npc_states = session.get("npc_states", {})
        current_location_name = session.get("current_location_name")
        location = game_world.get_location(current_location_name)
        
        if not location:
            logger.warning(f"sync_world_npcs: Location '{current_location_name}' not found in game_world!")
            return

        logger.info(f"sync_world_npcs: Checking location '{current_location_name}'. NPCs in world: {[c.name for c in location.characters]}")

        updates_needed = False
        for char in location.characters:
            if char.name not in npc_states:
                # Add missing NPC to session state
                npc_states[char.name] = {
                    "location": current_location_name,
                    "x": char.x,
                    "y": char.y,
                    "relationship": 50,
                    "mood": "content"
                }
                updates_needed = True
                logger.info(f"Synced missing NPC {char.name} to session {session_id}")
            elif char.name == "The Architect":
                # Force update debug NPC position
                if npc_states[char.name]["x"] != char.x or npc_states[char.name]["y"] != char.y:
                    npc_states[char.name]["x"] = char.x
                    npc_states[char.name]["y"] = char.y
                    updates_needed = True
                    logger.info(f"Force updated The Architect position to {char.x},{char.y}")
        
        if updates_needed:
            self._update_session_field(session_id, "npc_states", npc_states)

    def get_npcs_in_location(self, session_id: str) -> List[Dict]:
        # Sync NPCs first
        self.sync_world_npcs(session_id)
        
        session = self._get_session_data(session_id)
        if not session:
            return []

        current_location_name = session.get("current_location_name")
        player_x = session.get("player_x", 0)
        player_y = session.get("player_y", 0)
        
        npcs_here = []
        logger.info(f"Checking for NPCs in location: {current_location_name}")

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["location"] == current_location_name:
                npc_info = self.get_npc_info(npc_id)
                if npc_info:
                    dist = abs(npc_state['x'] - player_x) + abs(npc_state['y'] - player_y)
                    # Only include NPCs that are in the same tile or adjacent (distance <= 1)
                    if dist <= 1:
                        npcs_here.append({
                            "id": npc_id, 
                            "name": npc_info["name"], 
                            "short_description": npc_info["short_description"],
                            "x": npc_state['x'],
                            "y": npc_state['y'],
                            "distance": dist
                        })
        
        return npcs_here

    def get_map_display(self, session_id: str) -> Dict[str, Any]:
        """Generates a structured display of the map for the current location."""
        session = self._get_session_data(session_id)
        if not session:
            return {}

        location_name = session.get("current_location_name")
        location = game_world.get_location(location_name)
        
        if not location or not location.raw_layout:
            return {}

        player_x = session["player_x"]
        player_y = session["player_y"]

        map_grid = []
        for r_idx, row in enumerate(location.raw_layout):
            display_row = []
            for c_idx, cell in enumerate(row):
                has_npc = False
                for npc_id, npc_state in session["npc_states"].items():
                    if (npc_state["location"] == location_name and
                        npc_state["x"] == c_idx and
                        npc_state["y"] == r_idx):
                        has_npc = True
                        break
                
                tile_data = {
                    "char": cell,
                    "has_player": r_idx == player_y and c_idx == player_x,
                    "has_npc": has_npc
                }
                display_row.append(tile_data)
            map_grid.append(display_row)
        
        map_key = location.map_key if location.map_key else {}

        return {
            "grid": map_grid,
            "key": map_key
        }

    def initiate_dialogue(self, session_id: str, npc_name: str) -> str:
        session = self._get_session_data(session_id)
        if not session:
            return "Session not found."

        current_location_name = session.get("current_location_name")

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["location"] == current_location_name:
                npc_info = self.get_npc_info(npc_id)
                if npc_info and npc_info["name"].lower() == npc_name.lower():
                    logger.info(f"Initiating dialogue with {npc_id} in session {session_id}")
                    self._update_session_field(session_id, "conversation_partner", npc_id)
                    self.set_game_mode(session_id, "INTERACTION")
                    return f"You begin a conversation with {npc_info['name']}."
        
        return f"There is no one named {npc_name} here."

    def end_interaction(self, session_id: str):
        """Ends the current interaction and switches back to EXPLORATION mode."""
        logger.info(f"Ending interaction in session {session_id}")
        self._update_session_field(session_id, "conversation_partner", None)
        self.set_game_mode(session_id, "EXPLORATION")

    def get_game_mode(self, session_id: str) -> str:
        """Retrieves the current game mode."""
        data = self._get_session_data(session_id)
        return data.get("game_mode", "EXPLORATION") if data else "EXPLORATION"

    def set_game_mode(self, session_id: str, mode: str):
        """Sets the current game mode."""
        self._update_session_field(session_id, "game_mode", mode)

    def get_conversation_partner(self, session_id: str) -> str | None:
        data = self._get_session_data(session_id)
        return data.get("conversation_partner") if data else None

    def process_metadata(self, session_id: str, metadata: Dict[str, Any]):
        """Processes metadata from Gemini response to update game state."""
        if not metadata:
            return

        session = self._get_session_data(session_id)
        if not session:
            return

        # Update player stats
        if "player_stats" in metadata:
            for stat, value in metadata["player_stats"].items():
                if stat == "health":
                    self.set_health(session_id, value)
                elif stat == "gold":
                    self.set_gold(session_id, value)

        # Update inventory
        if "inventory_add" in metadata:
            for item in metadata["inventory_add"]:
                self.add_item_to_inventory(session_id, item)
        
        if "inventory_remove" in metadata:
            for item in metadata["inventory_remove"]:
                self.remove_item_from_inventory(session_id, item)

        # Update quest log
        if "quest_log_add" in metadata:
            for quest in metadata["quest_log_add"]:
                self.add_quest_to_log(session_id, quest)

        if "quest_log_remove" in metadata:
            for quest in metadata["quest_log_remove"]:
                self.remove_quest_from_log(session_id, quest)

    def get_active_session_id(self) -> str | None:
        """Retrieves an active session ID (for single-player/debug context)."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT session_id FROM sessions LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    # --- Challenge System Methods ---

    def get_player_stats(self, session_id: str) -> Dict[str, int]:
        """Retrieves player stats."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT strength, dexterity, intelligence, charisma FROM player_stats WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {"strength": 10, "dexterity": 10, "intelligence": 10, "charisma": 10}

    def add_quest(self, session_id: str, quest_data: Dict[str, Any]):
        """Adds a quest and its challenges to the database."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Insert Quest
        # Insert Quest (OR REPLACE to handle re-offers)
        cursor.execute('''
            INSERT OR REPLACE INTO quests (id, session_id, giver_npc, description, status, involved_entities, accept_response, refuse_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            quest_data['id'], 
            session_id, 
            quest_data['giver_npc'], 
            quest_data['description'], 
            quest_data.get('status', 'offered'), # Use provided status or default to offered
            json.dumps(quest_data.get('involved_entities', [])),
            quest_data.get('accept_response', "I'm glad you accepted."),
            quest_data.get('refuse_response', "That is unfortunate.")
        ))

        # Insert Challenges
        if 'challenges' in quest_data:
            for challenge in quest_data['challenges']:
                cursor.execute('''
                    INSERT OR REPLACE INTO challenges (id, session_id, quest_id, type, dc, description, completed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    challenge['id'], # Assuming ID is provided or generated
                    session_id,
                    quest_data['id'],
                    challenge['type'],
                    challenge['dc'],
                    challenge['description'],
                    False
                ))
        
        conn.commit()
        conn.close()

    def get_quest_log(self, session_id: str) -> List[str]:
        """Retrieves the player's quest log (titles/descriptions of active/completed quests)."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Fetch active and completed quests
        cursor.execute("SELECT description FROM quests WHERE session_id = ? AND status IN ('active', 'completed')", (session_id,))
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]

    def accept_quest(self, session_id: str, quest_id: str) -> str:
        """Updates quest status to 'active' and returns accept response."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get response text
        cursor.execute("SELECT accept_response FROM quests WHERE id = ? AND session_id = ?", (quest_id, session_id))
        row = cursor.fetchone()
        response_text = row['accept_response'] if row and row['accept_response'] else "Quest accepted."
        
        cursor.execute("UPDATE quests SET status = 'active' WHERE id = ? AND session_id = ?", (quest_id, session_id))
        conn.commit()
        conn.close()
        
        # Update Relationship
        giver = self.get_quest_giver(session_id, quest_id)
        if giver:
            self.update_relationship(session_id, giver, "quest_accepted")
            
        return response_text

    def refuse_quest(self, session_id: str, quest_id: str) -> str:
        """Updates quest status to 'refused' and returns refuse response."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get response text
        cursor.execute("SELECT refuse_response FROM quests WHERE id = ? AND session_id = ?", (quest_id, session_id))
        row = cursor.fetchone()
        response_text = row['refuse_response'] if row and row['refuse_response'] else "Quest refused."
        
        cursor.execute("UPDATE quests SET status = 'refused' WHERE id = ? AND session_id = ?", (quest_id, session_id))
        conn.commit()
        conn.close()
        
        # Update Relationship
        giver = self.get_quest_giver(session_id, quest_id)
        if giver:
            self.update_relationship(session_id, giver, "quest_refused")
            
        return response_text
        
        # Process involved entities
        if 'involved_entities' in quest_data:
            self.process_involved_entities(session_id, quest_data['involved_entities'])

    def get_quest_giver(self, session_id: str, quest_id: str) -> Optional[str]:
        """Retrieves the name of the NPC who gave the quest."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT giver_npc FROM quests WHERE id = ? AND session_id = ?", (quest_id, session_id))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def get_active_quests(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves active quests and their challenges."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM quests WHERE session_id = ? AND status IN ('active', 'completed', 'failed')", (session_id,))
        quests = [dict(row) for row in cursor.fetchall()]
        logger.info(f"get_active_quests for {session_id}: Found {len(quests)} quests. IDs: {[q['id'] for q in quests]}")
        
        # Get player stats for auto-resolution check
        stats = self.get_player_stats(session_id)
        
        for quest in quests:
            cursor.execute("SELECT * FROM challenges WHERE quest_id = ?", (quest['id'],))
            quest['challenges'] = [dict(row) for row in cursor.fetchall()]
            quest['involved_entities'] = json.loads(quest['involved_entities']) if quest['involved_entities'] else []
            
            # Calculate auto-result for active challenges
            if quest['status'] == 'active':
                for challenge in quest['challenges']:
                    if not challenge['completed']:
                        stat_value = stats.get(challenge['type'].lower(), 10)
                        auto_check = self.should_require_roll(challenge['dc'], stat_value)
                        if not auto_check['requires_roll']:
                            challenge['auto_result'] = auto_check['outcome']
                            challenge['auto_narrative'] = auto_check['narrative']
            
        conn.close()
        return quests

    def get_quest_context_for_npc(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves quests for NPC context (active, completed, failed). Excludes resolved."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM quests WHERE session_id = ? AND status IN ('active', 'completed', 'failed')", (session_id,))
        quests = [dict(row) for row in cursor.fetchall()]
        
        for quest in quests:
            cursor.execute("SELECT * FROM challenges WHERE quest_id = ?", (quest['id'],))
            challenges = [dict(row) for row in cursor.fetchall()]
            
            # Inject auto-resolution status for context
            if quest['status'] == 'active':
                player_stats = self.get_player_stats(session_id)
                for ch in challenges:
                    if not ch['completed']:
                        stat_key = ch['type'].lower()
                        player_stat = player_stats.get(stat_key, 10)
                        resolution = self.should_require_roll(ch['dc'], player_stat)
                        ch.update(resolution)
                        
            quest['challenges'] = challenges
            quest['involved_entities'] = json.loads(quest['involved_entities']) if quest['involved_entities'] else []
            
        conn.close()
        return quests

    def resolve_quest(self, session_id: str, quest_id: str):
        """Marks a quest as resolved (turned in)."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE quests SET status = 'resolved' WHERE id = ? AND session_id = ?", (quest_id, session_id))
        conn.commit()
        conn.close()

    def clear_dead_quests(self, session_id: str):
        """Force-resolves all quests that are 'completed' or 'failed'."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE quests SET status = 'resolved' WHERE session_id = ? AND status IN ('completed', 'failed')", (session_id,))
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count

    def resolve_challenge(self, session_id: str, challenge_id: str) -> Dict[str, Any]:
        """Resolves a challenge with dice mechanics."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get Challenge
        cursor.execute("SELECT * FROM challenges WHERE id = ? AND session_id = ?", (challenge_id, session_id))
        challenge_row = cursor.fetchone()
        
        if not challenge_row:
            conn.close()
            return {"error": "Challenge not found"}
            
        challenge = dict(challenge_row)
        
        # Get Stats
        stats = self.get_player_stats(session_id)
        stat_bonus = stats.get(challenge['type'].lower(), 0) # Raw score as bonus for now, or (score-10)//2
        # User said: "Success Threshold (dice roll + stat vs. DC)" - implying raw stat? 
        # "strength: 5" in example. D&D usually is modifier. 
        # But user example: "strength": 5. 
        # Let's assume raw stat for simplicity as per user prompt "roll(1d20) + player.stats[challenge_type]".
        
        # Auto-Resolution Check
        auto_check = self.should_require_roll(challenge['dc'], stat_bonus)
        
        if not auto_check['requires_roll']:
            success = auto_check['outcome'] == 'auto_success'
            roll = 20 if success else 1
            total = roll + stat_bonus
            result = {
                "success": success,
                "roll": roll,
                "stat_bonus": stat_bonus,
                "total": total,
                "dc": challenge['dc'],
                "margin": total - challenge['dc'],
                "challenge_type": challenge['type'],
                "description": challenge['description'],
                "critical_success": False,
                "critical_failure": False,
                "auto_resolved": True,
                "narrative": auto_check['narrative'],
                "severity": "minor" if success else "impossible"
            }
            # Relationship Update for Auto-Success
            if success:
                self.update_relationship(session_id, self.get_quest_giver(session_id, challenge['quest_id']), "auto_success")
        else:
            import random
            roll = random.randint(1, 20)
            total = roll + stat_bonus
            
            # Critical Failure Override
            if roll == 1:
                success = False
            else:
                success = total >= challenge['dc']
            
            # Determine Severity
            severity = "success"
            if not success:
                severity = self.calculate_failure_severity(total, challenge['dc'], roll == 1)
            
            # Relationship Update based on Outcome
            giver_name = self.get_quest_giver(session_id, challenge['quest_id'])
            if giver_name:
                if success:
                    self.update_relationship(session_id, giver_name, "roll_success")
                else:
                    # severity maps directly to modifier keys: minor -> minor_failure, etc.
                    modifier_key = f"{severity}_failure"
                    self.update_relationship(session_id, giver_name, modifier_key)

            result = {
                "success": success,
                "roll": roll,
                "stat_bonus": stat_bonus,
                "total": total,
                "dc": challenge['dc'],
                "margin": total - challenge['dc'],
                "challenge_type": challenge['type'],
                "description": challenge['description'],
                "critical_success": roll == 20,
                "critical_failure": roll == 1,
                "severity": severity
            }
        
        # Update Challenge
        cursor.execute('''
            UPDATE challenges 
            SET completed = ?, result = ? 
            WHERE id = ?
        ''', (True, json.dumps(result), challenge_id))
        
        conn.commit()
        
        # Check if all challenges for this quest are completed
        quest_id = challenge['quest_id']
        cursor.execute("SELECT COUNT(*) FROM challenges WHERE quest_id = ? AND completed = 0", (quest_id,))
        remaining = cursor.fetchone()[0]
        
        if remaining == 0:
            # All challenges completed! Check results.
            cursor.execute("SELECT result FROM challenges WHERE quest_id = ?", (quest_id,))
            results = cursor.fetchall()
            
            any_failure = False
            for res_row in results:
                if res_row[0]:
                    res_dict = json.loads(res_row[0])
                    if not res_dict.get('success', False):
                        any_failure = True
                        break
            
            new_status = 'failed' if any_failure else 'completed'
            cursor.execute("UPDATE quests SET status = ? WHERE id = ?", (new_status, quest_id))
            conn.commit()
            
        conn.close()
        
        return result

    def process_involved_entities(self, session_id: str, entities: List[str]):
        """Checks if entities exist, creates stubs if not."""
        # 1. Check Game World (Static)
        # 2. Check NPC States (Dynamic)
        # 3. Check Entity Stubs (Dynamic)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for entity_id in entities:
            # Check if known in game world
            if game_world.get_character(entity_id) or game_world.get_location(entity_id):
                continue
                
            # Check if exists in stubs
            cursor.execute("SELECT 1 FROM entity_stubs WHERE id = ? AND session_id = ?", (entity_id, session_id))
            if cursor.fetchone():
                continue
                
            # Create Stub
            stub_name = entity_id.replace("_", " ").title()
            cursor.execute('''
                INSERT INTO entity_stubs (id, session_id, name, type, status, needs_expansion)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (entity_id, session_id, stub_name, "unknown", "mentioned", True))
            logger.info(f"Created entity stub for: {entity_id}")
            
        conn.commit()
        conn.close()