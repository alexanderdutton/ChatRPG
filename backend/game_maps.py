from typing import Dict, List, Any

# Default starting map and coordinates
START_MAP_ID = "town_square"
START_X = 3
START_Y = 3

MAP_ELEMENTS = {
    '#': {"is_walkable": False, "feature_id": None, "is_significant_for_proximity": False},
    ' ': {"is_walkable": True, "feature_id": None, "is_significant_for_proximity": False},
    'D': {"is_walkable": True, "feature_id": None, "is_significant_for_proximity": True}, # Door - special handling for exits
    'T': {"is_walkable": True, "feature_id": "town_square_fountain", "is_significant_for_proximity": True},
    'H': {"is_walkable": True, "feature_id": "small_house", "is_significant_for_proximity": True},
    'B': {"is_walkable": True, "feature_id": "blacksmith", "is_significant_for_proximity": True},
    'M': {"is_walkable": True, "feature_id": "market", "is_significant_for_proximity": True},
    'V': {"is_walkable": True, "feature_id": "tavern", "is_significant_for_proximity": True},
    'F': {"is_walkable": True, "feature_id": "forest_path", "is_significant_for_proximity": True},
    'R': {"is_walkable": True, "feature_id": "main_road", "is_significant_for_proximity": True},
    'U': {"is_walkable": True, "feature_id": "unmade_bed", "is_significant_for_proximity": True},
    'C': {"is_walkable": True, "feature_id": "flickering_candle", "is_significant_for_proximity": True},
    'W': {"is_walkable": True, "feature_id": "worn_rug", "is_significant_for_proximity": True},
    'O': {"is_walkable": True, "feature_id": "cobweb_corner", "is_significant_for_proximity": True},
    'A': {"is_walkable": True, "feature_id": "anvil", "is_significant_for_proximity": True},
    'L': {"is_walkable": True, "feature_id": "tools", "is_significant_for_proximity": True},
    'X': {"is_walkable": True, "feature_id": "raw_materials", "is_significant_for_proximity": True},
    'Y': {"is_walkable": True, "feature_id": "water_bucket", "is_significant_for_proximity": True},
}

FEATURES = {
    "town_square_fountain": {
        "name": "fountain",
        "description_template": "A {name} gurgles in the center."
    },
    "small_house": {
        "name": "small house",
        "description_template": "There's a {name} here.",
        "type": "building",
        "associated_map_id": "house_interior"
    },
    "blacksmith": {
        "name": "blacksmith",
        "description_template": "You stand before a sturdy building with a smoking chimney. This must be the {name}.",
        "type": "building",
        "associated_map_id": "blacksmith_interior"
    },
    "market": {
        "name": "market",
        "description_template": "The {name} is filled with vendors hawking their wares."
    },
    "tavern": {
        "name": "tavern",
        "description_template": "The Rusty Flagon {name} is a cozy place, filled with the smell of ale and roasted meat."
    },
    "forest_path": {
        "name": "forest path",
        "description_template": "The old {name} winds into the trees."
    },
    "main_road": {
        "name": "main road",
        "description_template": "The {name} leads out of town."
    },
    "unmade_bed": {
        "name": "unmade bed",
        "description_template": "A small, {name}."
    },
    "flickering_candle": {
        "name": "flickering candle",
        "description_template": "A {name} on a wooden crate."
    },
    "worn_rug": {
        "name": "worn rug",
        "description_template": "A {name} on the floor."
    },
    "cobweb_corner": {
        "name": "cobweb-filled corner",
        "description_template": "A {name}."
    },
    "anvil": {
        "name": "anvil",
        "description_template": "An {name} stands ready for work."
    },
    "tools": {
        "name": "tools",
        "description_template": "Various {name} hang on the wall."
    },
    "raw_materials": {
        "name": "raw materials",
        "description_template": "A pile of {name}."
    },
    "water_bucket": {
        "name": "water bucket",
        "description_template": "A {name} for cooling."
    }
}

CHARACTERS = {
    "gregory": {
        "name": "Old Man Gregory",
        "race": "human",
        "occupation": "villager",
        "description": "A kindly old man with a long white beard and a thoughtful expression.",
        "personality_prompt": "You are Old Man Gregory. You have lived in this village your whole life and know its secrets. You are friendly, a bit forgetful, and speak in a folksy, rambling manner. You are worried about the increasing number of goblins in the nearby forest.",
        "movement_schedule": [
            {"map_id": "town_square", "x": 3, "y": 3},
            {"map_id": "town_square", "x": 5, "y": 4},
            {"map_id": "town_square", "x": 2, "y": 4},
        ]
    }
}

MAPS = {
    "town_square": {
        "name": "Town Square",
        "raw_layout": [
            "#######",
            "#M    #",
            "#     #",
            "#T H F#",
            "#     #",
            "#    R#",
            "#######",
        ],
        "element_mapping": {
            (3, 3): "H", # House
            (1, 1): "B", # Blacksmith
            (1, 3): "M", # Market
            (3, 1): "V", # Tavern
            (5, 3): "R", # Main Road
            (3, 5): "F", # Forest Path
            (3, 3): "T", # Town Square Fountain
        },
        "exits": {
            (3, 3): {"target_map_id": "house_interior"}, # House in town square
            (1, 1): {"target_map_id": "blacksmith_interior"}, # Blacksmith in town square
        }
    },
    "house_interior": {
        "name": "Small House Interior",
        "raw_layout": [
            "#####",
            "#U C#",
            "# D #",
            "#W O#",
            "#####",
        ],
        "element_mapping": {
            (1, 1): "U", # Unmade bed
            (1, 3): "C", # Flickering candle
            (3, 1): "W", # Worn rug
            (3, 3): "O", # Cobweb-filled corner
        },
        "exits": {
            (2, 2): {"target_map_id": "town_square", "target_x": 3, "target_y": 3}, # Door leads back to town square
        }
    },
    "blacksmith_interior": {
        "name": "Blacksmith's Forge",
        "raw_layout": [
            "#####",
            "#A L#",
            "# D #",
            "#X Y#",
            "#####",
        ],
        "element_mapping": {
            (1, 1): "A", # Anvil
            (1, 3): "L", # Tools
            (3, 1): "X", # Raw materials
            (3, 3): "Y", # Water bucket
        },
        "exits": {
            (2, 2): {"target_map_id": "town_square", "target_x": 1, "target_y": 1}, # Door leads back to town square
        }
    }
}

def build_map_data(map_id: str) -> Dict[str, Any]:
    map_info = MAPS[map_id]
    raw_layout = map_info["raw_layout"]
    element_mapping = map_info.get("element_mapping", {})
    exits = map_info.get("exits", {})

    layout = []
    descriptions = {}

    for r_idx, row_str in enumerate(raw_layout):
        row_list = []
        for c_idx, char in enumerate(row_str):
            # Determine the actual character at this position, considering element_mapping overrides
            actual_char = element_mapping.get((r_idx, c_idx), char)
            element_properties = MAP_ELEMENTS.get(actual_char, {"is_walkable": False, "feature_id": None, "is_significant_for_proximity": False})
            
            row_list.append(actual_char)

            # Add description if a feature is present and it's significant for proximity
            if element_properties["feature_id"] and element_properties["is_significant_for_proximity"]:
                descriptions[(r_idx, c_idx)] = {"feature": element_properties["feature_id"], "base_description": ""}
            elif actual_char == 'D': # Special handling for doors
                descriptions[(r_idx, c_idx)] = {"base_description": "You are standing at the door, which leads outside.", "feature": None}

        layout.append("".join(row_list))

    return {
        "name": map_info["name"],
        "layout": layout,
        "exits": exits,
        "descriptions": descriptions,
        "start_x": map_info.get("start_x", START_X),
        "start_y": map_info.get("start_y", START_Y)
    }
