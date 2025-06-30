from typing import Dict, List, Any
from game_maps import MAPS, FEATURES, START_MAP_ID, START_X, START_Y, build_map_data, MAP_ELEMENTS, CHARACTERS

class GameStateManager:

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        print("GameStateManager initialized.") # Trivial change to force reload - Attempt 2

    def find_door_coordinates(self, map_id: str) -> tuple[int, int] | None:
        """Finds the coordinates of the 'D' (door) in a given map's layout."""
        game_map = MAPS.get(map_id)
        if not game_map:
            return None
        
        layout = game_map.get("layout", [])
        for r_idx, row in enumerate(layout):
            for c_idx, cell in enumerate(row):
                if cell == 'D':
                    return r_idx, c_idx
        return None # No door found

    def create_session(self, session_id: str, player_name: str = "Traveler"):
        """Creates a new game session."""
        if session_id not in self.sessions:
            # Initialize NPC states
            npc_states = {}
            for npc_id, npc_data in CHARACTERS.items():
                initial_location = npc_data['movement_schedule'][0]
                npc_states[npc_id] = {
                    'map_id': initial_location['map_id'],
                    'x': initial_location['x'],
                    'y': initial_location['y'],
                    'movement_index': 0
                }

            self.sessions[session_id] = {
                "conversation_history": [],
                "player_name": player_name, # Use provided player name
                "current_map_id": START_MAP_ID,
                "player_x": START_X, # Starting X coordinate
                "player_y": START_Y, # Starting Y coordinate
                "inventory": [], # List of items the player possesses
                "health": 100, # Player's health points
                "gold": 0, # Player's gold count
                "quest_log": [], # List of active quests
                "npc_states": npc_states
            }
            print(f"Session {session_id} created with initial NPC states: {npc_states}")

    def session_exists(self, session_id: str) -> bool:
        """Checks if a session exists."""
        return session_id in self.sessions

    def get_conversation_history(self, session_id: str) -> List[Dict]:
        """Retrieves the conversation history for a given session."""
        print(f"Accessing conversation history for session {session_id}.")
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

    def get_current_location(self, session_id: str) -> str:
        """Retrieves the player's current location name for a given session."""
        session = self.sessions.get(session_id, {})
        map_id = session.get("current_map_id", START_MAP_ID)
        processed_map = build_map_data(map_id)
        return processed_map.get("name", "Unknown Location")

    def _get_feature_description(self, feature_id: str) -> str:
        """Generates a description for a given feature."""
        feature_info = FEATURES.get(feature_id)
        if feature_info:
            return feature_info["description_template"].format(name=feature_info["name"])
        return ""

    def get_current_location_description(self, session_id: str) -> str:
        """Retrieves the description of the player's current location, including nearby features."""
        session = self.sessions.get(session_id)
        if not session:
            return "You are in an unfamiliar place."

        # Update NPC positions before generating description
        self._update_npc_positions(session_id)

        map_id = session.get("current_map_id", START_MAP_ID)
        processed_map = build_map_data(map_id)
        descriptions_data = processed_map.get("descriptions", {})
        x, y = session.get("player_x"), session.get("player_y")

        current_cell_description_info = descriptions_data.get((x, y), {})
        
        # Start with the base description for the current cell
        description_parts = [current_cell_description_info.get("base_description", "")]

        # Add feature description for the current cell if applicable
        if "feature" in current_cell_description_info and current_cell_description_info["feature"] is not None:
            description_parts.append(self._get_feature_description(current_cell_description_info["feature"]))

        # Check for proximity descriptions for all maps
        directions = {
            (-1, 0): "north", (1, 0): "south", (0, -1): "west", (0, 1): "east",
            (-1, -1): "northwest", (-1, 1): "northeast", (1, -1): "southwest", (1, 1): "southeast"
        }

        for dx, dy in directions.keys():
            nx, ny = x + dx, y + dy

            # Check boundaries
            if not (0 <= nx < len(processed_map["layout"]) and 0 <= ny < len(processed_map["layout"][0])):
                continue

            # Get the character at the nearby position from the layout
            nearby_char = processed_map["layout"][nx][ny]
            nearby_element_properties = MAP_ELEMENTS.get(nearby_char)

            if nearby_element_properties and nearby_element_properties["feature_id"] and nearby_element_properties["is_significant_for_proximity"]:
                feature_id = nearby_element_properties["feature_id"]
                feature_name = FEATURES[feature_id]["name"]
                direction_word = directions[(dx, dy)]
                description_parts.append(f"To the {direction_word}, you see a {feature_name}.")

        # Filter out empty strings and join
        full_description = ". ".join(filter(None, description_parts)).strip()
        
        # Add NPC descriptions
        npc_states = session.get("npc_states", {})
        for npc_id, npc_state in npc_states.items():
            if npc_state["map_id"] == map_id and npc_state["x"] == x and npc_state["y"] == y:
                npc_info = CHARACTERS.get(npc_id)
                if npc_info:
                    full_description += f" You also see {npc_info['name']} here."
                    print(f"NPC {npc_info['name']} found at player location ({x},{y}) on map {map_id}.")

        if not full_description:
            return "You see nothing special here."
        return full_description

    def _update_npc_positions(self, session_id: str):
        """Updates the positions of all NPCs based on their movement schedules."""
        session = self.sessions.get(session_id)
        if not session or "npc_states" not in session:
            return

        for npc_id, npc_state in session["npc_states"].items():
            npc_data = CHARACTERS.get(npc_id)
            if not npc_data or not npc_data["movement_schedule"]:
                continue

            # Move to the next point in the schedule
            current_index = npc_state["movement_index"]
            next_index = (current_index + 1) % len(npc_data["movement_schedule"])
            
            next_location = npc_data["movement_schedule"][next_index]
            
            npc_state["map_id"] = next_location["map_id"]
            npc_state["x"] = next_location["x"]
            npc_state["y"] = next_location["y"]
            npc_state["movement_index"] = next_index

    def set_current_location(self, session_id: str, x: int, y: int):
        """Sets the player's current location coordinates for a given session."""
        if session_id in self.sessions:
            self.sessions[session_id]["player_x"] = x
            self.sessions[session_id]["player_y"] = y

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

    def get_quest_log(self, session_id: str) -> List[str]:
        """Retrieves the player's quest log for a given session."""
        return self.sessions.get(session_id, {}).get("quest_log", [])

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

        map_id = session["current_map_id"]
        processed_map = build_map_data(map_id)
        current_map_layout = processed_map["layout"]
        current_map_exits = processed_map["exits"]
        player_x = session["player_x"]
        player_y = session["player_y"]

        if command.startswith("go "):
            direction = command[3:].strip()
            
            new_x, new_y = player_x, player_y

            if direction == "north":
                new_x -= 1
            elif direction == "south":
                new_x += 1
            elif direction == "east":
                new_y += 1
            elif direction == "west":
                new_y -= 1
            else:
                return "Invalid direction. Use north, south, east, or west."

            # Check boundaries and impassable terrain
            if 0 <= new_x < len(current_map_layout) and 0 <= new_y < len(current_map_layout[0]):
                if current_map_layout[new_x][new_y] != '#':
                    self.set_current_location(session_id, new_x, new_y)
                    return f"You go {direction}. {self.get_current_location_description(session_id)}"
                else:
                    return "You can't go that way. It's blocked."
            else:
                return "You can't go that way. You've reached the edge of the world."

        elif command == "enter":
            if (player_x, player_y) in current_map_exits:
                exit_info = current_map_exits[(player_x, player_y)]
                new_map_id = exit_info["target_map_id"]
                
                # Check if the target map is an interior (has a 'D' for door)
                if self.find_door_coordinates(new_map_id):
                    door_coords = self.find_door_coordinates(new_map_id)
                    session["current_map_id"] = new_map_id
                    session["player_x"] = door_coords[0]
                    session["player_y"] = door_coords[1]
                    return f"You enter the {MAPS[new_map_id]['name']}. {self.get_current_location_description(session_id)}"
                else:
                    # If not an interior, use the target_x and target_y from the exit info
                    if "target_x" in exit_info and "target_y" in exit_info:
                        session["current_map_id"] = new_map_id
                        session["player_x"] = exit_info["target_x"]
                        session["player_y"] = exit_info["target_y"]
                        return f"You exit to the {MAPS[new_map_id]['name']}. {self.get_current_location_description(session_id)}"
                    else:
                        # Handle cases where the exit is defined but no specific coordinates are given
                        # Defaulting to a known safe point or the starting point of the new map
                        new_map_data = build_map_data(new_map_id)
                        session["current_map_id"] = new_map_id
                        session["player_x"] = new_map_data.get("start_x", 0) # Fallback to 0
                        session["player_y"] = new_map_data.get("start_y", 0) # Fallback to 0
                        return f"You transition to {MAPS[new_map_id]['name']}. {self.get_current_location_description(session_id)}"
            else:
                return "There is nothing to enter here."

        elif command == "inventory":
            inventory = self.get_inventory(session_id)
            if inventory:
                return f"Your inventory: {', '.join(inventory)}."
            else:
                return "Your inventory is empty."
        elif command == "look":
            return self.get_current_location_description(session_id)
        elif command.startswith("talk to "):
            npc_name = command[8:].strip()
            return self.initiate_dialogue(session_id, npc_name)
        else:
            return "I don't understand that command."

    def get_map_display(self, session_id: str) -> str:
        """Generates a text-based display of the map with the player's current position."""
        session = self.sessions.get(session_id)
        if not session:
            return ""
            
        map_id = session["current_map_id"]
        processed_map = build_map_data(map_id)
        current_map_layout = processed_map["layout"]
        player_x = session["player_x"]
        player_y = session["player_y"]
        
        map_display = []
        for r_idx, row in enumerate(current_map_layout):
            display_row = []
            for c_idx, cell in enumerate(row):
                if r_idx == player_x and c_idx == player_y:
                    display_row.append('P') # Player's position
                else:
                    display_row.append(cell)
            map_display.append("".join(display_row))
        return "\n".join(map_display)

    def get_npc_info(self, npc_id: str) -> Dict[str, Any]:
        return CHARACTERS.get(npc_id)

    def get_npcs_in_location(self, session_id: str) -> List[Dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []

        player_x = session["player_x"]
        player_y = session["player_y"]
        map_id = session["current_map_id"]
        npcs_here = []

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["map_id"] == map_id and npc_state["x"] == player_x and npc_state["y"] == player_y:
                npc_info = CHARACTERS.get(npc_id)
                if npc_info:
                    npcs_here.append({"id": npc_id, "name": npc_info["name"]})
        
        print(f"NPCs in location: {npcs_here}")
        return npcs_here

    def initiate_dialogue(self, session_id: str, npc_name: str) -> str:
        session = self.sessions.get(session_id)
        if not session:
            return "Session not found."

        player_x = session["player_x"]
        player_y = session["player_y"]
        map_id = session["current_map_id"]

        for npc_id, npc_state in session["npc_states"].items():
            if npc_state["map_id"] == map_id and npc_state["x"] == player_x and npc_state["y"] == player_y:
                npc_info = CHARACTERS.get(npc_id)
                if npc_info and npc_info["name"].lower() == npc_name.lower():
                    # Start conversation
                    session["conversation_partner"] = npc_id
                    return f"You begin a conversation with {npc_info['name']}."
        
        return f"There is no one named {npc_name} here."