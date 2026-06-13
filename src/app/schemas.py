from pydantic import BaseModel, Field
from typing import Any, Literal
from .scoring import ScoringSystem

class ApiResponse(BaseModel):
    ok: bool
    data: Any = None
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False
    error: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

class OptimizeRequest(BaseModel):
    phase: Literal["groups", "round_of_16", "quarter_finals", "semi_finals", "final"] = "groups"
    matchday: int = 1
    risk_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"
    objective: Literal["points", "value", "points_and_value"] = "points_and_value"
    score_system: ScoringSystem | None = None
    custom_ratings: dict[str, float] | None = None
    base_players: list[str] | None = Field(default=None, description="Optional list of player IDs that must be included in the optimized lineup")

class ComparePlayersRequest(BaseModel):
    player_ids: list[str] = Field(..., min_length=2, max_length=5, description="List of 2 to 5 player IDs to compare")
    score_system: ScoringSystem | None = None

class BuildPhasePlanRequest(BaseModel):
    phase: Literal["groups", "round_of_16", "quarter_finals", "semi_finals", "final"] = "groups"
    target_budget: float | None = Field(None, description="Optional target budget to plan towards")

class PlayerRatingUpdateRequest(BaseModel):
    manual_rating: float | None = Field(None, description="Custom rating value (or null to clear)")

class SuggestAlternativesRequest(BaseModel):
    player_id: str = Field(..., description="ID of the player to find alternatives for")
    max_price: float | None = Field(None, description="Optional maximum price limit in Millions (defaults to player price + 3.0M)")
    objective: Literal["points", "value", "points_and_value"] = "points_and_value"
    score_system: ScoringSystem | None = None

class SetLineupPayload(BaseModel):
    players: list[str] = Field(..., min_length=11, max_length=15, description="Ordered player IDs in the proposed squad")
    captain_id: str | None = Field(None, description="Optional captain player ID")
    ariete_id: str | None = Field(None, description="Optional ariete player ID")


class MakeBidPayload(BaseModel):
    player_id: str
    amount: int = Field(..., gt=0, description="Bid amount in absolute euros")


class PlayerActionPayload(BaseModel):
    player_id: str


class AcceptOfferPayload(BaseModel):
    offer_id: str


class SetLineupActionRequest(BaseModel):
    confirmation_token: str | None = None
    payload: SetLineupPayload


class MakeBidActionRequest(BaseModel):
    confirmation_token: str | None = None
    payload: MakeBidPayload


class PlayerActionRequest(BaseModel):
    confirmation_token: str | None = None
    payload: PlayerActionPayload


class AcceptOfferActionRequest(BaseModel):
    confirmation_token: str | None = None
    payload: AcceptOfferPayload

class SettingsUpdateRequest(BaseModel):
    total_budget: float | None = Field(None, description="Update total budget in millions (e.g. 920.0)")
    active_formation: str | None = Field(None, description="Update formation (e.g. 3-5-2)")
    max_players_same_team: int | None = Field(None, description="Update maximum players from same country")
    squad_size: int | None = Field(None, ge=11, le=15, description="Choose any squad size from 11 to 15")
    score_system: ScoringSystem | None = Field(None, description="Default scoring system for this admin workspace")


class PublicOptimizeRequest(OptimizeRequest):
    budget: float | None = Field(None, description="Available budget in Millions (e.g. 920.0). Defaults to phase rules.")
    formation: Literal["3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1"] = "3-5-2"
    max_players_same_team: int | None = Field(None, description="Max players from same country. Defaults to phase rules.")
    squad_size: int = Field(11, ge=11, le=15, description="Squad size (11 to 15)")
