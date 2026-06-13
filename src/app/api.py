from fastapi import APIRouter, Depends, Query, HTTPException, status
from .security import require_admin_api_key, require_api_key, require_user_id, ensure_write_allowed, lineup_confirmation_token, require_confirmation
from .schemas import (
    ApiResponse, OptimizeRequest, PublicOptimizeRequest,
    SettingsUpdateRequest, ComparePlayersRequest,
    BuildPhasePlanRequest, PlayerRatingUpdateRequest, SuggestAlternativesRequest,
    SetLineupActionRequest, MakeBidActionRequest, PlayerActionRequest,
    AcceptOfferActionRequest, MatchStatus, PlayerPosition, PlayerStatus,
)
from .core import core
from .rules import get_phase_rules, standings_warnings
from .optimizer import (
    optimize_lineup, analyze_captain_candidates, 
    analyze_ariete_candidates, find_better_alternatives
)
from .database import get_db_setting
from .config import settings
from .scoring import ScoringSystem


router = APIRouter(
    prefix="/api/users/{user_id}",
    dependencies=[Depends(require_admin_api_key), Depends(require_user_id)],
)

public_router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_api_key)],
)


async def _resolve_user_score_system(user_id: str, requested: ScoringSystem | None) -> ScoringSystem:
    if requested is not None:
        return requested
    return (await core.get_settings(user_id))["score_system"]


async def _public_score_system_required(score_system: ScoringSystem | None) -> ApiResponse | None:
    if score_system is not None:
        return None
    catalog = await core.get_scoring_systems()
    return ApiResponse(
        ok=False,
        error="SCORING_SYSTEM_REQUIRED",
        message="Choose a scoring system before calculating or comparing players.",
        details={"parameter": "score_system", "values": catalog["values"]},
        needs_review=True,
    )

@router.get("/status", response_model=ApiResponse, operation_id="getStatus")
async def status_route(user_id: str):
    return ApiResponse(ok=True, data=await core.status(user_id))

@router.get("/rules", response_model=ApiResponse, operation_id="getRules")
async def rules(user_id: str):
    """Return configurable example rules for this private league, not official Biwenger rules."""
    return ApiResponse(ok=True, data=await core.get_rules(user_id))

# --- Settings Management Endpoints ---

@router.get("/settings", response_model=ApiResponse, operation_id="getSettings")
async def get_settings(user_id: str):
    """Retrieve the current active budget and starting lineup formation settings."""
    cfg = await core.get_settings(user_id)
    return ApiResponse(ok=True, data=cfg)

@router.post("/settings", response_model=ApiResponse, operation_id="updateSettings")
async def update_settings(user_id: str, req: SettingsUpdateRequest):
    """Update settings (e.g. budget limit, starting lineup formation) dynamically."""
    try:
        updated = await core.update_settings(
            user_id=user_id,
            budget=req.total_budget,
            formation=req.active_formation,
            max_same_team=req.max_players_same_team,
            squad_size=req.squad_size,
            score_system=req.score_system,
        )
        return ApiResponse(ok=True, data=updated, message="Settings updated successfully.")
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ex)
        )

# --- Live Database Query & Sync Endpoints ---

async def _sync_database_impl() -> ApiResponse:
    res = await core.sync_database()
    if not res["synchronized"]:
        return ApiResponse(ok=False, error="SYNC_FAILED", message=res["message"])
    return ApiResponse(ok=True, data=res, message="Database synchronized successfully.")


@router.post("/sync", response_model=ApiResponse, operation_id="syncPublicData")
async def sync_database(user_id: str):
    """Trigger synchronization between the local database and the Biwenger API."""
    return await _sync_database_impl()


@router.get(
    "/sync",
    response_model=ApiResponse,
    operation_id="syncPublicDataViaGet",
    include_in_schema=True,
)
async def sync_database_via_get(user_id: str):
    """Compatibility alias for clients that call sync via GET."""
    return await _sync_database_impl()

@router.get("/players", response_model=ApiResponse, operation_id="listPlayers")
async def get_players(
    user_id: str,
    position: PlayerPosition | None = Query(None, description="Filter by position: GK, DEF, MID, FWD"),
    team: str | None = Query(None, description="Exact national-team filter value returned by GET /selections"),
    status: PlayerStatus | None = Query(None, description="Filter by availability status"),
    score_system: ScoringSystem | None = Query(None, description="Uses the admin setting when omitted; values come from GET /scoring-systems"),
    sort_by: str = Query("fixed_price", description="Sort by: fixed_price, points, market_value, goals, assists"),
    active_only: bool = Query(True, description="Filter to show only players whose national teams are still active in the tournament"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Fetch players with optional query filters, sorting, and pagination."""
    score_system = await _resolve_user_score_system(user_id, score_system)
    players = await core.get_players(
        user_id=user_id,
        position=position,
        team=team,
        status=status,
        score_system=score_system,
        sort_by=sort_by,
        active_only=active_only,
        limit=limit,
        offset=offset
    )
    return ApiResponse(ok=True, data=players)


@router.get("/selections", response_model=ApiResponse, operation_id="listSelections")
async def get_selections(user_id: str):
    """Return all national selections and the exact values used by the players filter."""
    return ApiResponse(ok=True, data=await core.get_selections())


@router.get("/scoring-systems", response_model=ApiResponse, operation_id="listScoringSystems")
async def get_scoring_systems(user_id: str):
    """Return all supported scoring systems and their request filter values."""
    return ApiResponse(ok=True, data=await core.get_scoring_systems())

@router.get("/player/{player_id}", response_model=ApiResponse, operation_id="getPlayer")
async def get_player(
    user_id: str,
    player_id: str,
    score_system: ScoringSystem | None = Query(None, description="Uses the admin setting when omitted; values come from GET /scoring-systems"),
):
    """Retrieve detailed stats for a specific player by ID."""
    score_system = await _resolve_user_score_system(user_id, score_system)
    player_data = await core.get_player(user_id, player_id, score_system)
    if player_data is None:
        return ApiResponse(
            ok=False,
            error="PLAYER_NOT_FOUND",
            message=f"Player {player_id} does not exist in the local catalogue.",
            needs_review=True,
        )
    return ApiResponse(ok=True, data=player_data)

@router.get("/matches", response_model=ApiResponse, operation_id="listMatches")
async def get_matches(
    user_id: str,
    round_name: str | None = Query(None, description="Filter by round name, e.g. 'Fase de Grupos, ronda 1'"),
    status: MatchStatus | None = Query(None, description="Filter by match status")
):
    """Fetch all match fixtures with optional round name or match status filtering."""
    matches = await core.get_matches(round_name=round_name, status=status)
    return ApiResponse(ok=True, data=matches)

@router.get("/standings", response_model=ApiResponse, operation_id="getStandings")
async def get_standings(user_id: str):
    """Retrieve group stage standings."""
    standings = await core.get_standings()
    warnings = standings_warnings(standings)
    return ApiResponse(ok=True, data=standings, warnings=warnings, needs_review=bool(warnings))

@router.get("/leagues", response_model=ApiResponse, operation_id="listLeagues")
async def get_leagues(user_id: str):
    """Retrieve active leagues."""
    return ApiResponse(ok=True, data=await core.get_leagues())

@router.get("/team", response_model=ApiResponse, operation_id="getAccountTeamAvailability")
async def get_team(user_id: str):
    """Retrieve the user's squad/roster stored in the SQLite database."""
    data = await core.get_my_team(user_id)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=not data["available"],
        warnings=[data["message"]] if not data["available"] else [],
    )

@router.get("/market", response_model=ApiResponse, operation_id="getFixedPriceCatalog")
async def get_market(user_id: str):
    """Retrieve the shared fixed-price catalogue, not a user's private live market."""
    return ApiResponse(ok=True, data=await core.get_market(user_id))

@router.post("/player/{player_id}/rating", response_model=ApiResponse, operation_id="setPlayerRating")
async def update_player_rating(user_id: str, player_id: str, req: PlayerRatingUpdateRequest):
    """Set or update the manual_rating for a player."""
    updated = await core.update_player_rating(user_id, player_id, req.manual_rating)
    if updated is None:
        return ApiResponse(
            ok=False,
            error="PLAYER_NOT_FOUND",
            message=f"Player {player_id} does not exist in the local catalogue.",
            needs_review=True,
        )
    return ApiResponse(ok=True, data=updated, message="Player manual rating updated successfully.")

@router.post("/compare-players", response_model=ApiResponse, operation_id="comparePlayers")
async def compare_players(user_id: str, req: ComparePlayersRequest):
    """Compare performance metrics of multiple players."""
    score_system = await _resolve_user_score_system(user_id, req.score_system)
    comparison = []
    for pid in req.player_ids:
        player_data = await core.get_player(user_id, pid, score_system)
        if player_data is None:
            return ApiResponse(
                ok=False,
                error="PLAYER_NOT_FOUND",
                message=f"Player {pid} does not exist in the local catalogue.",
                needs_review=True,
            )
        comparison.append({
            "id": player_data.get("id"),
            "name": player_data.get("name"),
            "position": player_data.get("position"),
            "team": player_data.get("team"),
            "fixed_price": player_data.get("fixed_price"),
            "points": player_data.get("points"),
            "goals": player_data.get("goals"),
            "assists": player_data.get("assists"),
            "num_reports": len(player_data.get("reports", []))
        })
    return ApiResponse(ok=True, data=comparison)

@router.post("/build-phase-plan", response_model=ApiResponse, operation_id="buildPhasePlan")
async def build_phase_plan(user_id: str, req: BuildPhasePlanRequest):
    """Build a roster planning guide from the configured fixed-price example rules."""
    rules = get_phase_rules(req.phase)
    budget = req.target_budget or rules["budget"]
    
    plan = {
        "phase": req.phase,
        "base_budget": budget,
        "max_same_team": rules["max_players_same_team"],
        "transfers_allowed": rules["transfers_allowed"],
        "captain_max_price": rules["captain_max_price"],
        "ariete_max_price": rules["ariete_max_price"],
        "strategy": [
            "1. Focus on high PPM value players to build core depth.",
            f"2. Keep selection from any single country <= {rules['max_players_same_team']} players.",
            "3. Identify and secure reliable starters (average minutes played >= 60)."
        ]
    }
    return ApiResponse(ok=True, data=plan)

# --- Optimization and Transfers ---

@router.post("/optimize-lineup", response_model=ApiResponse, operation_id="optimizeLineup")
async def optimize(user_id: str, req: OptimizeRequest):
    """Optimize 11 starters and 0-4 substitutes under the user's settings."""
    req = req.model_copy(update={"score_system": await _resolve_user_score_system(user_id, req.score_system)})
    data = await optimize_lineup(core, user_id, req)
    return ApiResponse(
        ok=True, 
        data=data, 
        needs_review=not bool(data.get("lineup")) or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )

@router.post("/pick-captain", response_model=ApiResponse, operation_id="pickCaptain")
async def pick_captain(user_id: str, req: OptimizeRequest):
    """Analyze and rank top candidates to select the best captain."""
    req = req.model_copy(update={"score_system": await _resolve_user_score_system(user_id, req.score_system)})
    data = await analyze_captain_candidates(core, user_id, req)
    return ApiResponse(
        ok=True, 
        data=data, 
        needs_review=data.get("best_candidate") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )

@router.post("/pick-ariete", response_model=ApiResponse, operation_id="pickAriete")
async def pick_ariete(user_id: str, req: OptimizeRequest):
    """Analyze and rank top candidates to select the best ariete."""
    req = req.model_copy(update={"score_system": await _resolve_user_score_system(user_id, req.score_system)})
    data = await analyze_ariete_candidates(core, user_id, req)
    return ApiResponse(
        ok=True, 
        data=data, 
        needs_review=data.get("best_candidate") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )

@router.post("/suggest-alternatives", response_model=ApiResponse, operation_id="suggestAlternatives")
async def suggest_alternatives(user_id: str, req: SuggestAlternativesRequest):
    """Suggest better or more cost-effective alternative players in the same position."""
    req = req.model_copy(update={"score_system": await _resolve_user_score_system(user_id, req.score_system)})
    data = await find_better_alternatives(core, user_id, req)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=data.get("target_player") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )

# --- Write Actions (Simulated locally in SQLite) ---

@router.post("/actions/set-lineup", response_model=ApiResponse, operation_id="simulateSetLineup")
async def set_lineup(user_id: str, req: SetLineupActionRequest):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    payload = req.payload.model_dump(exclude_none=True)
    expected = lineup_confirmation_token(user_id, payload)
    missing = require_confirmation(expected, req.confirmation_token)
    if missing:
        return missing

    return ApiResponse(ok=True, data=await core.set_lineup(user_id, payload))

@router.post("/actions/make-bid", response_model=ApiResponse, operation_id="simulateMakeBid")
async def make_bid(user_id: str, req: MakeBidActionRequest):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    payload = req.payload.model_dump()
    player_id = payload["player_id"]
    amount = payload["amount"]
    expected = f"CONFIRM_BID_{user_id}_{player_id}_{amount}"

    missing = require_confirmation(expected, req.confirmation_token)
    if missing:
        return missing

    return ApiResponse(ok=True, data=await core.make_bid(user_id, payload))

@router.post("/actions/cancel-bid", response_model=ApiResponse, operation_id="simulateCancelBid")
async def cancel_bid(user_id: str, req: PlayerActionRequest):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    payload = req.payload.model_dump()
    player_id = payload["player_id"]
    expected = f"CONFIRM_CANCEL_BID_{user_id}_{player_id}"

    missing = require_confirmation(expected, req.confirmation_token)
    if missing:
        return missing

    return ApiResponse(ok=True, data=await core.cancel_bid(user_id, payload))

@router.post("/actions/sell-player", response_model=ApiResponse, operation_id="simulateSellPlayer")
async def sell_player(user_id: str, req: PlayerActionRequest):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    payload = req.payload.model_dump()
    player_id = payload["player_id"]
    expected = f"CONFIRM_SELL_{user_id}_{player_id}"

    missing = require_confirmation(expected, req.confirmation_token)
    if missing:
        return missing

    return ApiResponse(ok=True, data=await core.sell_player(user_id, payload))

@router.post("/actions/accept-offer", response_model=ApiResponse, operation_id="simulateAcceptOffer")
async def accept_offer(user_id: str, req: AcceptOfferActionRequest):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    payload = req.payload.model_dump()
    offer_id = payload["offer_id"]
    expected = f"CONFIRM_ACCEPT_OFFER_{user_id}_{offer_id}"

    missing = require_confirmation(expected, req.confirmation_token)
    if missing:
        return missing

    return ApiResponse(ok=True, data=await core.accept_offer(user_id, payload))


@public_router.get("/status", response_model=ApiResponse, operation_id="getPublicStatus")
async def public_status_route():
    """Get the status of the API service without user isolation."""
    return ApiResponse(
        ok=True,
        data={
            "interface_mode": settings.interface_mode,
            "price_mode": "fixed_only",
            "competition_slug": settings.biwenger_competition_slug,
            "write_actions_enabled": True,
            "database_initialized": True,
            "last_successful_sync": get_db_setting("last_successful_sync"),
        }
    )


@public_router.get("/rules", response_model=ApiResponse, operation_id="getPublicRules")
async def public_rules():
    """Retrieve the configurable private-league example rules without user context."""
    from .rules import get_example_ruleset
    return ApiResponse(ok=True, data=get_example_ruleset())


@public_router.get("/players", response_model=ApiResponse, operation_id="listPublicPlayers")
async def get_public_players(
    position: PlayerPosition | None = Query(None, description="Filter by position: GK, DEF, MID, FWD"),
    team: str | None = Query(None, description="Exact national-team filter value returned by GET /api/selections"),
    status: PlayerStatus | None = Query(None, description="Filter by availability status"),
    score_system: ScoringSystem | None = Query(None, description="Required; values come from GET /api/scoring-systems"),
    sort_by: str = Query("fixed_price", description="Sort by: fixed_price, points, market_value, goals, assists"),
    active_only: bool = Query(True, description="Filter to show only players whose national teams are still active in the tournament"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Fetch players with optional query filters, sorting, and pagination (public catalog)."""
    missing = await _public_score_system_required(score_system)
    if missing:
        return missing
    players = await core.get_players(
        user_id=None,
        position=position,
        team=team,
        status=status,
        score_system=score_system,
        sort_by=sort_by,
        active_only=active_only,
        limit=limit,
        offset=offset
    )
    return ApiResponse(ok=True, data=players)


@public_router.get("/selections", response_model=ApiResponse, operation_id="listPublicSelections")
async def get_public_selections():
    """Return all national selections and the exact values used by the public players filter."""
    return ApiResponse(ok=True, data=await core.get_selections())


@public_router.get("/scoring-systems", response_model=ApiResponse, operation_id="listPublicScoringSystems")
async def get_public_scoring_systems():
    """Return all supported scoring systems and their request filter values."""
    return ApiResponse(ok=True, data=await core.get_scoring_systems())


@public_router.get("/player/{player_id}", response_model=ApiResponse, operation_id="getPublicPlayer")
async def get_public_player(
    player_id: str,
    score_system: ScoringSystem | None = Query(None, description="Required; values come from GET /api/scoring-systems"),
):
    """Retrieve detailed stats for a specific player by ID (public catalog)."""
    missing = await _public_score_system_required(score_system)
    if missing:
        return missing
    player_data = await core.get_player(None, player_id, score_system)
    if player_data is None:
        return ApiResponse(
            ok=False,
            error="PLAYER_NOT_FOUND",
            message=f"Player {player_id} does not exist in the local catalogue.",
            needs_review=True,
        )
    return ApiResponse(ok=True, data=player_data)


@public_router.get("/matches", response_model=ApiResponse, operation_id="listPublicMatches")
async def get_public_matches(
    round_name: str | None = Query(None, description="Filter by round name, e.g. 'Fase de Grupos, ronda 1'"),
    status: MatchStatus | None = Query(None, description="Filter by match status")
):
    """Fetch all match fixtures with optional round name or match status filtering."""
    matches = await core.get_matches(round_name=round_name, status=status)
    return ApiResponse(ok=True, data=matches)


@public_router.get("/standings", response_model=ApiResponse, operation_id="getPublicStandings")
async def get_public_standings():
    """Retrieve group stage standings."""
    standings = await core.get_standings()
    warnings = standings_warnings(standings)
    return ApiResponse(ok=True, data=standings, warnings=warnings, needs_review=bool(warnings))


@public_router.get("/leagues", response_model=ApiResponse, operation_id="listPublicLeagues")
async def get_public_leagues():
    """Retrieve active leagues."""
    return ApiResponse(ok=True, data=await core.get_leagues())


@public_router.post("/optimize-lineup", response_model=ApiResponse, operation_id="optimizePublicLineup")
async def public_optimize(req: PublicOptimizeRequest):
    """Optimize 11 starters and 0-4 substitutes with custom budget/formation/squad size parameters without saving/loading user settings (stateless)."""
    missing = await _public_score_system_required(req.score_system)
    if missing:
        return missing
    try:
        d, m, f = core.parse_formation(req.formation)
    except ValueError:
        d, m, f = 4, 4, 2
    
    rules = get_phase_rules(req.phase)
    budget = req.budget or rules["budget"]
    max_same_team = req.max_players_same_team or rules["max_players_same_team"]
    
    override_settings = {
        "total_budget": budget,
        "active_formation": req.formation,
        "max_players_same_team": max_same_team,
        "num_gk": 1,
        "num_def": d,
        "num_mid": m,
        "num_fwd": f,
        "squad_size": req.squad_size,
        "num_substitutes": req.squad_size - 11,
        "total_players": req.squad_size,
    }
    
    data = await optimize_lineup(core, settings.default_user_id, req, override_settings=override_settings)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=not bool(data.get("lineup")) or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )


@public_router.post("/suggest-alternatives", response_model=ApiResponse, operation_id="suggestPublicAlternatives")
async def public_suggest_alternatives(req: SuggestAlternativesRequest):
    """Suggest better or more cost-effective alternative players in the same position (stateless)."""
    missing = await _public_score_system_required(req.score_system)
    if missing:
        return missing
    data = await find_better_alternatives(core, settings.default_user_id, req)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=data.get("target_player") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )


@public_router.post("/pick-captain", response_model=ApiResponse, operation_id="pickPublicCaptain")
async def public_pick_captain(req: OptimizeRequest):
    """Analyze and rank top candidates to select the best captain (stateless)."""
    missing = await _public_score_system_required(req.score_system)
    if missing:
        return missing
    data = await analyze_captain_candidates(core, settings.default_user_id, req)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=data.get("best_candidate") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )


@public_router.post("/pick-ariete", response_model=ApiResponse, operation_id="pickPublicAriete")
async def public_pick_ariete(req: OptimizeRequest):
    """Analyze and rank top candidates to select the best ariete (stateless)."""
    missing = await _public_score_system_required(req.score_system)
    if missing:
        return missing
    data = await analyze_ariete_candidates(core, settings.default_user_id, req)
    return ApiResponse(
        ok=True,
        data=data,
        needs_review=data.get("best_candidate") is None or bool(data.get("needs_review")),
        warnings=data.get("explanation", [])
    )


@public_router.post("/compare-players", response_model=ApiResponse, operation_id="comparePublicPlayers")
async def public_compare_players(req: ComparePlayersRequest):
    """Compare performance metrics of multiple players (stateless)."""
    missing = await _public_score_system_required(req.score_system)
    if missing:
        return missing
    comparison = []
    for pid in req.player_ids:
        player_data = await core.get_player(settings.default_user_id, pid, req.score_system)
        if player_data is None:
            return ApiResponse(
                ok=False,
                error="PLAYER_NOT_FOUND",
                message=f"Player {pid} does not exist in the local catalogue.",
                needs_review=True,
              )
        comparison.append({
            "id": player_data.get("id"),
            "name": player_data.get("name"),
            "position": player_data.get("position"),
            "team": player_data.get("team"),
            "fixed_price": player_data.get("fixed_price"),
            "points": player_data.get("points"),
            "goals": player_data.get("goals"),
            "assists": player_data.get("assists"),
            "num_reports": len(player_data.get("reports", []))
        })
    return ApiResponse(ok=True, data=comparison)


@public_router.post("/build-phase-plan", response_model=ApiResponse, operation_id="buildPublicPhasePlan")
async def public_build_phase_plan(req: BuildPhasePlanRequest):
    """Build a roster planning guide from the configured fixed-price example rules (stateless)."""
    rules = get_phase_rules(req.phase)
    budget = req.target_budget or rules["budget"]
    
    plan = {
        "phase": req.phase,
        "base_budget": budget,
        "max_same_team": rules["max_players_same_team"],
        "transfers_allowed": rules["transfers_allowed"],
        "captain_max_price": rules["captain_max_price"],
        "ariete_max_price": rules["ariete_max_price"],
        "strategy": [
            "1. Focus on high PPM value players to build core depth.",
            f"2. Keep selection from any single country <= {rules['max_players_same_team']} players.",
            "3. Identify and secure reliable starters (average minutes played >= 60)."
        ]
    }
    return ApiResponse(ok=True, data=plan)
