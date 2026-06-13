"""
MCP server skeleton.

Requires:
    pip install fastmcp

Run only when:
    INTERFACE_MODE=mcp
"""

from .config import settings
from .core import core
from .schemas import OptimizeRequest
from .optimizer import (
    optimize_lineup, analyze_captain_candidates, 
    analyze_ariete_candidates, find_better_alternatives
)
from .security import ensure_write_allowed, lineup_confirmation_token, require_confirmation
from .rules import get_phase_rules

try:
    from fastmcp import FastMCP
except ImportError:
    FastMCP = None


if settings.interface_mode != "mcp":
    raise RuntimeError("INTERFACE_MODE must be 'mcp' to run MCP server.")

if FastMCP is None:
    raise RuntimeError("fastmcp is not installed. Install it to run MCP mode.")

mcp = FastMCP("biwenger-agent")


@mcp.tool()
async def biwenger_status(user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    return {"ok": True, "data": await core.status(user_id)}


@mcp.tool()
async def biwenger_get_rules(user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    return {"ok": True, "data": await core.get_rules(user_id)}


@mcp.tool()
async def biwenger_get_leagues(user_id: str | None = None) -> dict:
    return {"ok": True, "data": await core.get_leagues()}


@mcp.tool()
async def biwenger_get_my_team(user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    data = await core.get_my_team(user_id)
    return {
        "ok": True,
        "data": data,
        "needs_review": not data["available"],
        "warnings": [data["message"]] if not data["available"] else [],
    }


@mcp.tool()
async def biwenger_get_market(user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    return {"ok": True, "data": await core.get_market(user_id)}


@mcp.tool()
async def biwenger_get_standings(user_id: str | None = None) -> dict:
    return {"ok": True, "data": await core.get_standings()}


@mcp.tool()
async def biwenger_get_player(
    player_id: str,
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    player = await core.get_player(user_id, player_id, score_system)
    if player is None:
        return {
            "ok": False,
            "error": "PLAYER_NOT_FOUND",
            "message": f"Player {player_id} does not exist in the local catalogue.",
            "needs_review": True,
        }
    return {"ok": True, "data": player}


@mcp.tool()
async def biwenger_get_players(
    position: str | None = None,
    team: str | None = None,
    status: str | None = None,
    score_system: str | None = None,
    sort_by: str = "fixed_price",
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    user_id: str | None = None,
) -> dict:
    """Fetch players with optional query filters, sorting, and pagination."""
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
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
    return {"ok": True, "data": players}


@mcp.tool()
async def biwenger_optimize_lineup(
    phase: str = "groups",
    matchday: int = 1,
    risk_profile: str = "balanced",
    objective: str = "points_and_value",
    score_system: str | None = None,
    base_players: list[str] | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    req = OptimizeRequest(
        phase=phase,
        matchday=matchday,
        risk_profile=risk_profile,
        objective=objective,
        score_system=score_system,
        base_players=base_players,
    )
    data = await optimize_lineup(core, user_id, req)
    return {
        "ok": True,
        "data": data,
        "needs_review": not bool(data.get("lineup")) or bool(data.get("needs_review")),
    }


@mcp.tool()
async def biwenger_pick_captain(
    phase: str = "groups",
    matchday: int = 1,
    risk_profile: str = "balanced",
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    req = OptimizeRequest(phase=phase, matchday=matchday, risk_profile=risk_profile, score_system=score_system)
    data = await analyze_captain_candidates(core, user_id, req)
    return {"ok": True, "data": data, "needs_review": data.get("best_candidate") is None}


@mcp.tool()
async def biwenger_pick_ariete(
    phase: str = "groups",
    matchday: int = 1,
    risk_profile: str = "balanced",
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    req = OptimizeRequest(phase=phase, matchday=matchday, risk_profile=risk_profile, score_system=score_system)
    data = await analyze_ariete_candidates(core, user_id, req)
    return {"ok": True, "data": data, "needs_review": data.get("best_candidate") is None}


@mcp.tool()
async def biwenger_compare_players(
    player_ids: list[str],
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    comparison = []
    for pid in player_ids:
        player_data = await core.get_player(user_id, pid, score_system)
        if player_data is None:
            return {
                "ok": False,
                "error": "PLAYER_NOT_FOUND",
                "message": f"Player {pid} does not exist in the local catalogue.",
                "needs_review": True,
            }
        comparison.append({
            "id": player_data.get("id"),
            "name": player_data.get("name"),
            "position": player_data.get("position"),
            "team": player_data.get("team"),
            "fixed_price": player_data.get("fixed_price"),
            "points": player_data.get("points"),
            "goals": player_data.get("goals"),
            "assists": player_data.get("assists")
        })
    return {"ok": True, "data": comparison}


@mcp.tool()
async def biwenger_build_phase_plan(phase: str, target_budget: float | None = None, user_id: str | None = None) -> dict:
    rules = get_phase_rules(phase)
    budget = target_budget or rules["budget"]
    plan = {
        "phase": phase,
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
    return {"ok": True, "data": plan}


@mcp.tool()
async def biwenger_set_lineup(payload: dict, confirmation_token: str | None = None, user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    blocked = ensure_write_allowed()
    if blocked:
        return blocked
    expected = lineup_confirmation_token(user_id, payload)
    missing = require_confirmation(expected, confirmation_token)
    if missing:
        return missing
    return {"ok": True, "data": await core.set_lineup(user_id, payload)}


@mcp.tool()
async def biwenger_make_bid(player_id: str, amount: int, confirmation_token: str | None = None, user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    expected = f"CONFIRM_BID_{user_id}_{player_id}_{amount}"
    missing = require_confirmation(expected, confirmation_token)
    if missing:
        return missing

    return {"ok": True, "data": await core.make_bid(user_id, {"player_id": player_id, "amount": amount})}


@mcp.tool()
async def biwenger_cancel_bid(player_id: str, confirmation_token: str | None = None, user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    blocked = ensure_write_allowed()
    if blocked:
        return blocked
    expected = f"CONFIRM_CANCEL_BID_{user_id}_{player_id}"
    missing = require_confirmation(expected, confirmation_token)
    if missing:
        return missing
    return {"ok": True, "data": await core.cancel_bid(user_id, {"player_id": player_id})}


@mcp.tool()
async def biwenger_sell_player(player_id: str, confirmation_token: str | None = None, user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    blocked = ensure_write_allowed()
    if blocked:
        return blocked
    expected = f"CONFIRM_SELL_{user_id}_{player_id}"
    missing = require_confirmation(expected, confirmation_token)
    if missing:
        return missing
    return {"ok": True, "data": await core.sell_player(user_id, {"player_id": player_id})}


@mcp.tool()
async def biwenger_accept_offer(offer_id: str, confirmation_token: str | None = None, user_id: str | None = None) -> dict:
    user_id = user_id or settings.default_user_id
    blocked = ensure_write_allowed()
    if blocked:
        return blocked
    expected = f"CONFIRM_ACCEPT_OFFER_{user_id}_{offer_id}"
    missing = require_confirmation(expected, confirmation_token)
    if missing:
        return missing
    return {"ok": True, "data": await core.accept_offer(user_id, {"offer_id": offer_id})}


@mcp.tool()
async def biwenger_suggest_alternatives(
    player_id: str,
    max_price: float | None = None,
    objective: str = "points_and_value",
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    user_id = user_id or settings.default_user_id
    score_system = score_system or (await core.get_settings(user_id))["score_system"]
    class SimpleReq:
        def __init__(self, p_id, m_price, obj, scoring):
            self.player_id = p_id
            self.max_price = m_price
            self.objective = obj
            self.score_system = scoring
            
    req = SimpleReq(player_id, max_price, objective, score_system)
    data = await find_better_alternatives(core, user_id, req)
    return {"ok": True, "data": data}


@mcp.tool()
async def biwenger_update_settings(
    total_budget: float | None = None,
    active_formation: str | None = None,
    max_players_same_team: int | None = None,
    squad_size: int | None = None,
    score_system: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Update configurations in settings (e.g. budget, active formation layout)."""
    user_id = user_id or settings.default_user_id
    try:
        updated = await core.update_settings(
            user_id=user_id,
            budget=total_budget,
            formation=active_formation,
            max_same_team=max_players_same_team,
            squad_size=squad_size,
            score_system=score_system,
        )
        return {"ok": True, "data": updated, "message": "Settings updated successfully."}
    except Exception as ex:
        return {"ok": False, "error": "INVALID_SETTINGS", "message": str(ex)}


if __name__ == "__main__":
    mcp.run()
