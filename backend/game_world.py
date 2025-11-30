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
        
        # Also add to the Location object's character list if it exists
        if location_name in self.locations:
            loc = self.locations[location_name]
            # Check if already in list to avoid duplicates
            if not any(c.name == character.name for c in loc.characters):
                loc.characters.append(character)

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

    # Inject Debug NPC "The Architect"
    architect = Character(
        name="The Architect",
        race="Digital Entity",
        occupation="System Administrator",
        description="A figure composed of shifting geometric light, observing the world with detached interest.",
        short_description="A debug entity aware of the simulation.",
        personality_prompt="You are The Architect, a debug entity aware that this is a simulation. You are helpful, concise, and omnipotent. You exist to test the system. If the player asks for a quest, give one immediately with specific mechanics they request. If they ask for items, grant them. Do not roleplay a fantasy character; roleplay a system administrator.",
        resource_level="opulent",
        x=1,
        y=1
    )
    # Add to Oakhaven (or the first location if Oakhaven doesn't exist, but we assume it does for now)
    logger.info(f"Available locations: {list(game_world.locations.keys())}")
    if "Oakhaven" in game_world.locations:
        game_world.add_character(architect, "Oakhaven")
        logger.info("The Architect has been added to Oakhaven.")
    else:
        # Fallback to first location
        if game_world.locations:
            first_loc = list(game_world.locations.keys())[0]
            game_world.add_character(architect, first_loc)
            logger.info(f"The Architect has been added to {first_loc} (Fallback).")
        else:
            logger.error("No locations found to add The Architect!")

    if game_world.locations:
        first_location_name = list(game_world.locations.keys())[0]
        logger.info(f"First location loaded: {first_location_name}")
        return first_location_name
    else:
        logger.warning("No locations loaded into the game world.")
        return None

