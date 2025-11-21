import os
import json
import logging
from typing import Dict, List, Any
from pydantic import ValidationError

from .models import GameWorldData, Location, Character

logger = logging.getLogger(__name__)


class GameWorld:
    def __init__(self):
        self.name: str | None = None
        self.locations: Dict[str, Location] = {}
        self.characters: Dict[str, Dict[str, Character]] = {}

    def add_location(self, location: Location):
        self.locations[location.name] = location

    def add_character(self, character: Character, location_name: str):
        if location_name not in self.characters:
            self.characters[location_name] = {}
        self.characters[location_name][character.name] = character

    def get_location(self, name: str) -> Location | None:
        return self.locations.get(name)

    def get_character(self, name: str, location_name: str | None = None) -> Character | None:
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

def initialize_game_world(world_name: str | None = None):
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
            game_data_dict = json.load(f)
        
        # Validate and load data using Pydantic model
        game_data = GameWorldData.parse_obj(game_data_dict)

    except FileNotFoundError:
        logger.error(f"World file not found: {file_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decoding error in {world_name}.json: {e}")
        raise  # Re-raise the exception after logging
    except ValidationError as e:
        logger.error(f"Validation error for world data in {world_name}.json: {e}")
        raise # Re-raise the exception after logging

    game_world.name = game_data.world

    for location in game_data.locations:
        game_world.add_location(location)

        for character in location.characters:
            game_world.add_character(character, location.name)

    if game_world.locations:
        first_location_name = list(game_world.locations.keys())[0]
        logger.info(f"First location loaded: {first_location_name}")
        return first_location_name
    else:
        logger.warning("No locations loaded into the game world.")
        return None

