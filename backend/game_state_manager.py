import sqlite3
import json
import os
from typing import Dict, List, Any
from .game_world import game_world
from .gemini_service import generate_item_details, generate_quest
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "game_data.db")

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
                conversation_partner TEXT
            )
        ''')
        conn.commit()
        conn.close()

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
                inventory, health, gold, quest_log, npc_states, conversation_history, conversation_partner
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, player_name, first_location_name, player_start_x, player_start_y,
            json.dumps([]), 100, 0, json.dumps([]), json.dumps(npc_states), json.dumps([]), None
        ))
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
        directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        for direction, (dx, dy) in directions.items():
            adj_x, adj_y = player_x + dx, player_y + dy
            if 0 <= adj_y < len(location.raw_layout) and 0 <= adj_x < len(location.raw_layout[0]):
                adj_cell_char = location.raw_layout[adj_y][adj_x]
                for feature in location.features:
                    if feature.name == location.map_key.get(adj_cell_char):
                        adjacent_features_list.append(f"To the {direction} there is {feature.name}.")
                        break
        if adjacent_features_list:
            description_parts["adjacent_features_description"] = " ".join(adjacent_features_list)

        # Add NPC descriptions
        npcs_in_location = self.get_npcs_in_location(session_id)
        if npcs_in_location:
            npc_descriptions = []
            for npc in npcs_in_location:
                npc_state = session["npc_states"][npc["id"]]
                if npc_state["x"] == player_x and npc_state["y"] == player_y:
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
        elif command.startswith("inspect "):
            item_name = command[8:].strip()
            inventory = self.get_inventory(session_id)
            # Simple check if item is in inventory (partial match)
            found_item = next((i for i in inventory if item_name.lower() in i.lower()), None)
            if found_item:
                return await generate_item_details(found_item)
            else:
                return "You don't have that item."
        elif command == "rumors":
            location_desc = self.get_current_location_description(session_id)
            context = f"Location: {location_desc['location_description']}. {location_desc['npcs_description']}"
            return await generate_quest(context)
        else:
            return "I don't understand that command."

    def get_npc_info(self, npc_id: str) -> Dict[str, Any]:
        character = game_world.get_character(npc_id)
        return character.dict() if character else None

    def get_npcs_in_location(self, session_id: str) -> List[Dict]:
        session = self._get_session_data(session_id)
        if not session:
            return []

        current_location_name = session.get("current_location_name")
        npcs_here = []
        logger.info(f"Checking for NPCs in location: {current_location_name}")

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["location"] == current_location_name:
                npc_info = self.get_npc_info(npc_id)
                if npc_info:
                    npcs_here.append({"id": npc_id, "name": npc_info["name"], "short_description": npc_info["short_description"]})
        
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
                    self._update_session_field(session_id, "conversation_partner", npc_id)
                    return f"You begin a conversation with {npc_info['name']}."
        
        return f"There is no one named {npc_name} here."

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