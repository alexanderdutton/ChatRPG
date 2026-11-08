from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Optional, Literal
import logging

logger = logging.getLogger(__name__)

ChallengeType = Literal["strength", "dexterity", "intelligence", "charisma"]
ChallengeTier = Literal["trivial", "average", "tough", "elite", "boss", "legendary"]
ChallengeDifficulty = Literal["easy", "medium", "hard", "heroic"]

class Feature(BaseModel):
    name: str
    description: str

class PlayerStats(BaseModel):
    strength: int = 10
    dexterity: int = 10
    intelligence: int = 10
    charisma: int = 10

class Challenge(BaseModel):
    """A quest step, as stored. The model never writes `dc` or `quest_id`:
    it proposes a tier and/or difficulty label, and the engine assigns the
    DC from the difficulty table and fills in the parent quest id on
    commit (see DIFFICULTY_TABLE in gemini_service.py)."""
    id: str
    quest_id: Optional[str] = None
    type: ChallengeType
    tier: Optional[ChallengeTier] = None
    difficulty: Optional[ChallengeDifficulty] = None
    dc: Optional[int] = None  # engine-assigned, never model-supplied
    description: str
    completed: bool = False

class Quest(BaseModel):
    id: str
    description: str
    giver_npc: str
    tier: Optional[str] = None
    accept_response: Optional[str] = None
    refuse_response: Optional[str] = None
    rewards: Optional[Dict[str, Any]] = None
    involved_entities: List[str] = []
    # "active", "completed", "failed", "resolved", "withdrawn"
    # ("withdrawn" = a severe failure made the giver abandon the quest;
    # see the failing-forward rules in the challenge prompt.)
    status: str = "active"
    challenges: List[Challenge] = []

class Character(BaseModel):
    name: str
    race: str
    occupation: str
    description: str
    personality_prompt: str
    resource_level: str = "poor"
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

# ---- Turn schema (slice 1) -------------------------------------------------
# One structured JSON turn from the model, requested in JSON mode and
# Pydantic-validated. Replaces the regex extraction in
# extract_json_metadata for conversation. The model proposes; the engine
# validates and commits. The model never writes a DC anywhere in here.

class QuestOffer(BaseModel):
    """A quest as the model offers it. `dc` appears nowhere: challenges
    carry a tier and/or difficulty label, and the engine assigns DCs."""
    id: str
    description: str
    giver_npc: Optional[str] = None  # engine fills from the NPC in play
    tier: ChallengeTier = "average"
    accept_response: str = ""
    refuse_response: str = ""
    challenges: List[Challenge] = []
    involved_entities: List[str] = []
    rewards: Optional[Dict[str, Any]] = None

class SkillCheckProposal(BaseModel):
    """An immediate check during conversation. Label only — the engine
    computes the DC from the difficulty table."""
    type: ChallengeType
    difficulty: ChallengeDifficulty
    description: str
    success_response: str
    failure_response: str

class Claim(BaseModel):
    """One factual assertion the model made in its dialogue, so the engine
    can check it against world state. `kind` is the model's own hedge:
    "fact" (stated as true), "inference" (reasoned from what it knows),
    "rumor" (heard secondhand, may be wrong)."""
    text: str
    kind: Literal["fact", "inference", "rumor"] = "fact"
    subject: Optional[str] = None

class NewEntity(BaseModel):
    """Something the model invented in dialogue that is not in the world
    yet. Proposed, not committed — the engine validates, deduplicates,
    and commits it (propose-validate-commit).

    `kind` is taxonomy, not mechanics, so an unknown value coerces to
    "other" (with a warning) instead of voiding the whole turn — the
    verification replay caught a model writing "thing" for a flower, which
    under strict validation lost an otherwise perfect quest offer.
    """
    kind: Literal["character", "location", "item", "other"]
    name: str
    description: Optional[str] = None
    location: Optional[str] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, v):
        if v not in ("character", "location", "item", "other"):
            logger.warning(f"New entity kind {v!r} is not a known kind; treating as 'other'.")
            return "other"
        return v

class Referral(BaseModel):
    """The NPC directing the player to someone more knowledgeable:
    "ask Joren the trapper, he's seen the man." The referred person or
    place is in the world or generated on demand with its own knowledge."""
    to_name: str
    to_kind: Literal["person", "place"] = "person"
    reason: str = ""

    @field_validator("to_kind", mode="before")
    @classmethod
    def _coerce_to_kind(cls, v):
        # Taxonomy, not mechanics: coerce rather than void the turn.
        if v not in ("person", "place"):
            logger.warning(f"Referral to_kind {v!r} is not a known kind; treating as 'person'.")
            return "person"
        return v

class TurnResponse(BaseModel):
    """One conversation turn, whole. `dialogue` is what the player sees;
    everything else is the engine's business."""
    dialogue: str
    quest_offered: Optional[QuestOffer] = None
    quest_accepted: Optional[str] = None
    quest_resolved: Optional[str] = None
    skill_check: Optional[SkillCheckProposal] = None
    new_entities: List[NewEntity] = []
    referrals: List[Referral] = []
    claims: List[Claim] = []
    memory_update: List[str] = []
