from typing import Dict, List, Any
from game_world import game_world
import logging

logger = logging.getLogger(__name__)

class GameStateManager:

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        print("GameStateManager initialized.")

    def create_session(self, session_id: str, player_name: str = "Traveler"):
        """Creates a new game session."""
        if session_id not in self.sessions:
            # Initialize NPC states
            npc_states = {}
            for location_name, location_obj in game_world.locations.items():
                for character_name, character_obj in \
                        game_world.characters.get(location_name, {}).items():
                    npc_states[character_name] = {
                        'location': location_name,
                        'x': character_obj.x,
                        'y': character_obj.y
                    }

            # Get the first location from the loaded world
            first_location_name = list(game_world.locations.keys())[0]
            first_location = game_world.get_location(first_location_name)

            self.sessions[session_id] = {
                "conversation_history": [],
                "player_name": player_name,
                "current_location_name": first_location_name,
                "player_x": first_location.player_initial_location["x"],
                "player_y": first_location.player_initial_location["y"],
                "inventory": [],
                "health": 100,
                "gold": 0,
                "quest_log": [],
                "npc_states": npc_states
            }
            print(f"Session {session_id} created with initial NPC states: {npc_states}")

    def session_exists(self, session_id: str) -> bool:
        """Checks if a session exists."""
        return session_id in self.sessions

    def get_conversation_history(self, session_id: str) -> List[Dict]:
        """Retrieves the conversation history for a given session."""
        return self.sessions.get(session_id, {}).get("conversation_history", [])

    def update_conversation_history(self, session_id: str, history: List[Dict]):
        """Updates the conversation history for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["conversation_history"] = history

    def get_player_name(self, session_id: str) -> str:
        """Retrieves the player's name for a given session."""
        return self.sessions.get(session_id, {}).get("player_name", "Traveler")

    def set_player_name(self, session_id: str, name: str):
        """Sets the player's name for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["player_name"] = name

    def get_current_location_name(self, session_id: str) -> str:
        """Retrieves the player's current location name for a given session."""
        return self.sessions.get(session_id, {}).get("current_location_name", "Unknown Location")

    def set_current_location_name(self, session_id: str, location_name: str):
        """Sets the player's current location name for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["current_location_name"] = location_name
            # Update player's x and y to the initial location of the new room
            new_location = game_world.get_location(location_name)
            if new_location and new_location.player_initial_location:
                self.sessions[session_id]["player_x"] =                     new_location.player_initial_location["x"]
                self.sessions[session_id]["player_y"] =                     new_location.player_initial_location["y"]

    def get_current_location_description(self, session_id: str) -> Dict[str, str]:
        """Retrieves the description components of the player's current location."""
        session = self.sessions.get(session_id)
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
            if feature["name"] == location.map_key.get(current_cell_char):
                description_parts["current_feature_description"] = f"Located here is {feature['description']}"
                break

        # Add descriptions of notable features in adjacent squares
        adjacent_features_list = []
        directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        for direction, (dx, dy) in directions.items():
            adj_x, adj_y = player_x + dx, player_y + dy
            if 0 <= adj_y < len(location.raw_layout) and 0 <= adj_x < len(location.raw_layout[0]):
                adj_cell_char = location.raw_layout[adj_y][adj_x]
                for feature in location.features:
                    if feature["name"] == location.map_key.get(adj_cell_char):
                        adjacent_features_list.append(f"To the {direction} there is {feature['name']}.")
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
        return self.sessions.get(session_id, {}).get("inventory", [])

    def add_item_to_inventory(self, session_id: str, item: str):
        """Adds an item to the player's inventory for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["inventory"].append(item)

    def remove_item_from_inventory(self, session_id: str, item: str):
        """Removes an item from the player's inventory for a given session."""
        if session_id in self.sessions and item in self.sessions[session_id]["inventory"]:
            self.sessions[session_id]["inventory"].remove(item)

    def get_health(self, session_id: str) -> int:
        """Retrieves the player's health for a given session."""
        return self.sessions.get(session_id, {}).get("health", 100)

    def set_health(self, session_id: str, health: int):
        """Sets the player's health for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["health"] = health

    def get_gold(self, session_id: str) -> int:
        """Retrieves the player's gold for a given session."""
        return self.sessions.get(session_id, {}).get("gold", 0)

    def set_gold(self, session_id: str, gold: int):
        """Sets the player's gold for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["gold"] = gold

    def move_player(self, session_id: str, direction: str) -> str:
        session = self.sessions.get(session_id)
        if not session:
            return "Session not found."

        current_location_name = self.get_current_location_name(session_id)
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

        session["player_x"] = new_x
        session["player_y"] = new_y

        response_message = f"You move {direction}."

        # Get current feature description
        current_cell_char = location.raw_layout[new_y][new_x]
        for feature in location.features:
            if feature["name"] == location.map_key.get(current_cell_char):
                response_message += f"\nYou are standing on: {feature['description']}"
                break

        # Get adjacent feature names
        adjacent_features_list = []
        directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        for dir_name, (dx, dy) in directions.items():
            adj_x, adj_y = new_x + dx, new_y + dy
            if 0 <= adj_y < len(location.raw_layout) and 0 <= adj_x < len(location.raw_layout[0]):
                adj_cell_char = location.raw_layout[adj_y][adj_x]
                for feature in location.features:
                    if feature["name"] == location.map_key.get(adj_cell_char):
                        adjacent_features_list.append(f"To the {dir_name} is {feature['name']}.")
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
        session = self.sessions.get(session_id)
        if not session:
            return "Session not found."

        current_location_name = self.get_current_location_name(session_id)
        location = game_world.get_location(current_location_name)

        if not location:
            return "You are in an unknown location."

        player_x = session["player_x"]
        player_y = session["player_y"]

        # Check if player is on an exit tile
        # This assumes exits are marked in raw_layout and map_key, or are features
        # For now, let's assume exits are defined in location.exits and we need to find a matching coordinate
        # This is a simplified approach and might need more robust logic depending on how exits are defined.
        
        # For now, let's just check if there's any exit from the current location
        if location.exits:
            # For simplicity, let's just pick the first available exit for now
            # A more complex game would require specifying which exit to take
            for direction, exit_location_name in location.exits.items():
                if exit_location_name:
                    new_location = game_world.get_location(exit_location_name)
                    if new_location:
                        self.set_current_location_name(session_id, exit_location_name)
                        # Reset player position to initial location in new room
                        session["player_x"] = new_location.player_initial_location["x"]
                        session["player_y"] = new_location.player_initial_location["y"]
                        return f"You enter {new_location.name}. {new_location.description}"
            return "There are no accessible exits from your current position."
        else:
            return "There are no exits from this location."

    def get_quest_log(self, session_id: str) -> List[str]:
        """Retrieves the player's quest log for a given session."""
        return self.sessions.get(session_id, {}).get(
            "quest_log", []
        )

    def add_quest_to_log(self, session_id: str, quest: str):
        """Adds a quest to the player's quest log for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["quest_log"].append(quest)

    def remove_quest_from_log(self, session_id: str, quest: str):
        """Removes a quest from the player's quest log for a given session."""
        if session_id in self.sessions and quest in self.sessions[session_id]["quest_log"]:
            self.sessions[session_id]["quest_log"].remove(quest)

    def process_command(self, session_id: str, command: str) -> str:
        """Processes a game command and returns a response."""
        command = command.lower().strip()
        session = self.sessions.get(session_id)
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
        else:
            return "I don't understand that command."

    def get_npc_info(self, npc_id: str) -> Dict[str, Any]:
        character = game_world.get_character(npc_id)
        return character.to_dict() if character else None

    def get_npcs_in_location(self, session_id: str) -> List[Dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []

        current_location_name = self.get_current_location_name(session_id)
        npcs_here = []
        logger.info(f"Checking for NPCs in location: {current_location_name}")

        for npc_id, npc_state in session["npc_states"].items():
            logger.info(f"Processing NPC: {npc_id}, State: {npc_state}")
            if npc_state["location"] == current_location_name:
                npc_info = self.get_npc_info(npc_id)
                if npc_info:
                    npcs_here.append({"id": npc_id, "name": npc_info["name"], "short_description": npc_info["short_description"]})
                    logger.info(f"Found NPC in location: {npc_info['name']}")
        
        logger.info(f"NPCs found in {current_location_name}: {npcs_here}")
        return npcs_here

    def get_map_display(self, session_id: str) -> Dict[str, Any]:
        """Generates a structured display of the map for the current location."""
        session = self.sessions.get(session_id)
        if not session:
            return {}

        location_name = session.get("current_location_name")
        location = game_world.get_location(location_name)
        if not location or not location.raw_layout:
            logger.warning(f"Map data not found for location: {location_name}")
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
        session = self.sessions.get(session_id)
        if not session:
            return "Session not found."

        current_location_name = self.get_current_location_name(session_id)

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["location"] == current_location_name:
                npc_info = self.get_npc_info(npc_id)
                if npc_info and npc_info["name"].lower() == npc_name.lower():
                    session["conversation_partner"] = npc_id
                    return f"You begin a conversation with {npc_info['name']}."
        
        return f"There is no one named {npc_name} here."

    def process_metadata(self, session_id: str, metadata: Dict[str, Any]):
        """Processes metadata from Gemini response to update game state."""
        if not metadata:
            return

        session = self.sessions.get(session_id)
        if not session:
            return

        # Update player stats
        if "player_stats" in metadata:
            for stat, value in metadata["player_stats"].items():
                if stat == "health":
                    session["health"] = value
                elif stat == "gold":
                    session["gold"] = value

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