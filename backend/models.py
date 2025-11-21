from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class Feature(BaseModel):
    name: str
    description: str

class Character(BaseModel):
    name: str
    race: str
    occupation: str
    description: str
    personality_prompt: str
    x: int
    y: int
    short_description: str = Field(..., description="A brief, one-sentence description of the character for quick reference.")

class Location(BaseModel):
    name: str
    description: str
    raw_layout: List[str]
    map_key: Dict[str, str]
    player_initial_location: Dict[str, int]
    exits: Dict[str, str] = {}
    features: List[Feature] = []
    characters: List[Character] = []

class GameWorldData(BaseModel):
    world: str
    description: str
    locations: List[Location]
