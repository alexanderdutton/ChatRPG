import os
import json
import logging

logger = logging.getLogger(__name__)


class Location:
    def __init__(self, name, description, exits=None, features=None,
                 raw_layout=None, map_key=None, player_initial_location=None):
        self.name = name
        self.description = description
        self.exits = exits if exits is not None else {}
        self.features = features if features is not None else []
        self.raw_layout = raw_layout if raw_layout is not None else []
        self.map_key = map_key if map_key is not None else {}
        self.player_initial_location = player_initial_location if player_initial_location is not None else {}

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "exits": self.exits,
            "features": self.features,
            "raw_layout": self.raw_layout,
            "map_key": self.map_key,
            "player_initial_location": self.player_initial_location
        }

class Character:
    def __init__(self, name, race, occupation, description, personality_prompt, x, y, short_description=None):
        self.name = name
        self.race = race
        self.occupation = occupation
        self.description = description
        self.personality_prompt = personality_prompt
        self.x = x
        self.y = y
        self.short_description = short_description if short_description is not None else ""

    def to_dict(self):
        return {
            "name": self.name,
            "race": self.race,
            "occupation": self.occupation,
            "description": self.description,
            "personality_prompt": self.personality_prompt,
            "x": self.x,
            "y": self.y,
            "short_description": self.short_description
        }

class GameWorld:
    def __init__(self):
        self.name = None
        self.locations = {}
        self.characters = {}

    def add_location(self, location):
        self.locations[location.name] = location

    def add_character(self, character, location_name):
        if location_name not in self.characters:
            self.characters[location_name] = {}
        self.characters[location_name][character.name] = character

    def get_location(self, name):
        return self.locations.get(name)

    def get_character(self, name, location_name=None):
        if location_name:
            return self.characters.get(location_name, {}).get(name)
        else:
            # Search all locations for the character
            for loc_chars in self.characters.values():
                if name in loc_chars:
                    return loc_chars[name]
            return None

# Initializing the game world
game_world = GameWorld()

def initialize_game_world(world_name: str = None):
    """Loads all game data and populates the game world."""
    game_world.locations = {}  # Clear existing data
    game_world.characters = {} # Clear existing data
    logger.info("Game world data cleared.")

    if not world_name:
        world_name = "Elodia" # Default world to load

    game_world.name = world_name

    # Load from a specific world file
    file_path = os.path.join(os.path.dirname(__file__), "worlds", f"{world_name}.json")
    try:
        with open(file_path, 'r') as f:
            game_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decoding error in {world_name}.json: {e}")
        raise  # Re-raise the exception after logging

    for loc_data in game_data.get("locations", []):
        location = Location(
            name=loc_data.get("name"),
            description=loc_data.get("description"),
            exits=loc_data.get("exits"),
            features=loc_data.get("features"),
            raw_layout=loc_data.get("raw_layout"),
            map_key=loc_data.get("map_key"),
            player_initial_location=loc_data.get("player_initial_location")
        )
        game_world.add_location(location)

        for char_data in loc_data.get("characters", []):
            character = Character(
                name=char_data.get("name"),
                race=char_data.get("race"),
                occupation=char_data.get("occupation"),
                description=char_data.get("description"),
                personality_prompt=char_data.get("personality_prompt"),
                x=char_data.get("x"),
                y=char_data.get("y"),
                short_description=char_data.get("short_description", ""))
            game_world.add_character(character, location.name)

    if game_world.locations:
        first_location_name = list(game_world.locations.keys())[0]
        logger.info(f"First location loaded: {first_location_name}")
        return first_location_name
    else:
        logger.warning("No locations loaded into the game world.")
        return None

