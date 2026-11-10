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

class KnowledgeEntry(BaseModel):
    """One thing an NPC knows, as world state with provenance (§14).

    `source` is in-world provenance — how the NPC learned it. `confidence`
    is how sure the NPC is. Both are recorded and shown to the model, but
    neither changes the modifier yet; that is the gossip layer, deferred.
    `dc_modifier` is what the player knowing this fact does to a check
    about its `topic` (usually negative — knowledge makes the check
    easier); `applies_to` scopes it to one challenge type, or None for
    any type about the topic. Modifiers are clamped to [-10, 10] rather
    than voiding a turn. `id` is optional: the engine assigns one when
    a generated NPC's knowledge omits it.
    """
    id: Optional[str] = None
    topic: str
    fact: str
    source: Literal["saw", "heard_from", "everyone_says"] = "heard_from"
    confidence: Literal["high", "medium", "low"] = "medium"
    dc_modifier: int = 0
    applies_to: Optional[ChallengeType] = None

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, v):
        if v not in ("saw", "heard_from", "everyone_says"):
            logger.warning(f"Knowledge source {v!r} is not known; treating as 'heard_from'.")
            return "heard_from"
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        if v not in ("high", "medium", "low"):
            logger.warning(f"Knowledge confidence {v!r} is not known; treating as 'medium'.")
            return "medium"
        return v

    @field_validator("dc_modifier", mode="before")
    @classmethod
    def _clamp_modifier(cls, v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            logger.warning(f"Knowledge dc_modifier {v!r} is not a number; treating as 0.")
            return 0
        if n < -10 or n > 10:
            logger.warning(f"Knowledge dc_modifier {n} is out of range; clamping to [-10, 10].")
            return max(-10, min(10, n))
        return n

class Character(BaseModel):
    # `id` and `provenance` are engine-set at commit time (slice 5,
    # write-back, §22). Shipped entities keep both as None and their
    # name as the key, so the world file is backward compatible.
    id: Optional[str] = None
    name: str
    race: str
    occupation: str
    description: str
    personality_prompt: str
    resource_level: str = "poor"
    x: int
    y: int
    short_description: str = Field(..., description="A brief, one-sentence description of the character for quick reference.")
    provenance: Optional[Dict[str, Any]] = None
    # What this NPC knows, as world state with provenance (§14, slice 6).
    # Seeded in the world file for shipped NPCs; committed with the
    # character for generated ones.
    knowledge: List[KnowledgeEntry] = []

class Location(BaseModel):
    # See Character: `id` and `provenance` are engine-set at commit.
    id: Optional[str] = None
    name: str
    description: str
    raw_layout: List[str]
    map_key: Dict[str, str]
    player_initial_location: Dict[str, int]
    exits: Dict[str, str] = {}
    features: List[Feature] = []
    characters: List[Character] = []
    provenance: Optional[Dict[str, Any]] = None

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
    # The person or thing the check is about (e.g. "Corvin"), so the
    # engine can apply what the player has learned about them (§14,
    # slice 6). Optional — without it, the engine falls back to
    # matching learned topics against the description.
    subject: Optional[str] = None

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
    # What this invented person knows (§14, slice 6): the model proposes
    # it, the engine validates the structure and commits it with the
    # character, so a generated NPC arrives with knowledge the player
    # can learn from them. Only meaningful for kind "character".
    knowledge: List[KnowledgeEntry] = []

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
    # The ids of this NPC's knowledge entries it shared with the player
    # this turn (§14, slice 6). The engine validates them against the
    # NPC's actual set — a claim the engine can check, unlike free text.
    knowledge_shared: List[str] = []
