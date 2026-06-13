from collections import Counter

import pytest

from src.app.core import core, matches_from_active_events, normalize_player_status
from src.app.database import (
    get_connection, get_matches_from_db, get_players_from_db, init_db,
    save_matches_to_db, save_players_to_db,
)
from src.app.optimizer import analyze_ariete_candidates, get_player_advanced_score, optimize_lineup
from src.app.rules import get_phase_rules
from src.app.schemas import OptimizeRequest


def build_players() -> list[dict]:
    players = []
    positions = ("GK", "DEF", "MID", "FWD")
    for team_index in range(1, 9):
        for position_index, position in enumerate(positions, start=1):
            players.append({
                "id": f"{team_index}-{position}",
                "name": f"Player {team_index} {position}",
                "slug": None,
                "position": position,
                "team": f"Team {team_index}",
                "fixed_price": 4.0 + team_index + position_index / 10,
                "fixed_price_source": "test",
                "market_value": 999.0,
                "points": 100 - team_index - position_index,
                "status": "ok",
            })
    return players


def test_non_sofascore_calculation_does_not_mix_sofascore_report_form():
    player = {
        "id": "score-test",
        "position": "MID",
        "status": "ok",
        "fixed_price": 10.0,
        "points": 40,
        "goals": 0,
        "assists": 0,
    }
    reports = [{"points": 1, "date": 1, "minutes_played": 90}]

    sofascore_result = get_player_advanced_score(
        player, reports, {}, None, objective="points", score_system="sofascore"
    )
    statistics_result = get_player_advanced_score(
        player, reports, {}, None, objective="points", score_system="statistics"
    )

    assert sofascore_result == 22.35
    assert statistics_result == 36.0


def test_active_events_keep_their_real_round_name():
    matches = matches_from_active_events([{
        "name": "Fase de Grupos, ronda 1",
        "games": [{
            "id": 10,
            "date": 123,
            "status": "preview",
            "home": {"id": 1, "name": "A", "score": None},
            "away": {"id": 2, "name": "B", "score": None},
        }],
    }])

    assert matches[10]["round_name"] == "Fase de Grupos, ronda 1"
    assert matches[10]["status"] == "preview"


def test_biwenger_doubt_status_is_normalized():
    assert normalize_player_status("doubt") == "doubtful"
    assert normalize_player_status("ok") == "ok"


@pytest.fixture(autouse=True)
def seeded_database():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM player_reports")
        conn.execute("DELETE FROM standings")
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM players")
        conn.execute("DELETE FROM user_squad_players")
        conn.execute("DELETE FROM user_settings")
        conn.execute("DELETE FROM user_player_ratings")
        conn.execute("UPDATE settings SET value = '920.0' WHERE key = 'total_budget'")
        conn.execute("UPDATE settings SET value = '4-4-2' WHERE key = 'active_formation'")
        conn.execute("UPDATE settings SET value = '3' WHERE key = 'max_players_same_team'")
        conn.commit()
    save_players_to_db(build_players())


@pytest.mark.asyncio
async def test_optimizer_builds_valid_fixed_price_starting_xi():
    result = await optimize_lineup(core, "alice", OptimizeRequest())

    assert len(result["lineup"]) == 11
    assert result["substitutes"] == []
    assert Counter(p["position"] for p in result["lineup"]) == {
        "GK": 1, "DEF": 4, "MID": 4, "FWD": 2,
    }
    squad = result["lineup"] + result["substitutes"]
    assert max(Counter(p["team"] for p in squad).values()) <= 3
    assert sum(p["fixed_price"] for p in squad) <= result["rule_checks"]["budget_limit"]
    assert result["captain"]["position"] != "GK"
    assert result["captain"]["fixed_price"] <= 70.0
    assert result["ariete"]["position"] == "FWD"
    assert result["ariete"]["fixed_price"] <= 90.0
    assert result["needs_review"] is True
    assert result["rule_checks"]["squad_size"] == 11


@pytest.mark.parametrize(
    ("phase", "budget", "max_same_team", "transfers", "captain", "ariete"),
    [
        ("groups", 920.0, 3, 3, 70.0, 90.0),
        ("round_of_16", 980.0, 3, 4, 85.0, 105.0),
        ("quarter_finals", 1040.0, 4, 4, 100.0, 120.0),
        ("semi_finals", 1100.0, 5, 5, 115.0, 135.0),
        ("final", 1160.0, 6, 6, 130.0, 150.0),
    ],
)
def test_example_rules_are_applied_per_phase(phase, budget, max_same_team, transfers, captain, ariete):
    rules = get_phase_rules(phase)
    assert rules["budget"] == budget
    assert rules["max_players_same_team"] == max_same_team
    assert rules["transfers_allowed"] == transfers
    assert rules["captain_max_price"] == captain
    assert rules["ariete_max_price"] == ariete


@pytest.mark.asyncio
@pytest.mark.parametrize("squad_size", [12, 13, 14, 15])
async def test_optimizer_supports_every_squad_size(squad_size):
    await core.update_settings("alice", squad_size=squad_size)
    result = await optimize_lineup(core, "alice", OptimizeRequest())

    assert len(result["lineup"]) == 11
    assert len(result["substitutes"]) == squad_size - 11
    assert len(Counter(p["position"] for p in result["substitutes"])) == squad_size - 11
    assert len(result["lineup"] + result["substitutes"]) == squad_size
    assert result["rule_checks"]["squad_size"] == squad_size


@pytest.mark.asyncio
async def test_invalid_formation_is_rejected():
    with pytest.raises(ValueError):
        await core.update_settings("alice", formation="invalid")
    with pytest.raises(ValueError):
        await core.update_settings("alice", formation="2-2-6")


def test_player_filters_accept_all_supported_fields():
    with get_connection() as conn:
        conn.execute("UPDATE players SET team = 'España', status = 'warned' WHERE id = '1-GK'")
        conn.commit()

    assert get_players_from_db("alice", position="GK")
    assert get_players_from_db("alice", team="espana")[0]["team"] == "España"
    assert get_players_from_db("alice", status="WARNED")[0]["status"] == "warned"


def test_match_filters_are_case_and_accent_insensitive():
    save_matches_to_db([{
        "id": 100,
        "round_name": "Fase de Grupos, ronda 1",
        "date": 1,
        "status": "injuryTime",
        "home_team_id": 1,
        "home_team_name": "España",
        "home_score": 1,
        "away_team_id": 2,
        "away_team_name": "México",
        "away_score": 0,
    }])

    assert get_matches_from_db(status="injurytime")[0]["id"] == 100
    assert get_matches_from_db(round_name="fase de grupos, ronda 1")[0]["id"] == 100


@pytest.mark.asyncio
async def test_ariete_candidates_are_forwards_only():
    result = await analyze_ariete_candidates(core, "alice", OptimizeRequest())

    assert result["candidates"]
    assert all(candidate["player"]["position"] == "FWD" for candidate in result["candidates"])


@pytest.mark.asyncio
async def test_user_settings_are_isolated():
    await core.update_settings("alice", squad_size=12, budget=900)
    await core.update_settings("bob", squad_size=15, budget=1100)

    alice = await core.get_settings("alice")
    bob = await core.get_settings("bob")
    assert (alice["squad_size"], alice["total_budget"]) == (12, 900)
    assert (bob["squad_size"], bob["total_budget"]) == (15, 1100)


@pytest.mark.asyncio
async def test_manual_ratings_are_isolated_per_user():
    await core.update_player_rating("alice", "1-GK", 9.5)
    await core.update_player_rating("bob", "1-GK", 4.0)

    alice = await core.get_player("alice", "1-GK")
    bob = await core.get_player("bob", "1-GK")
    charlie = await core.get_player("charlie", "1-GK")
    assert alice["manual_rating"] == 9.5
    assert bob["manual_rating"] == 4.0
    assert charlie["manual_rating"] is None


@pytest.mark.asyncio
async def test_optimizer_respects_base_players():
    req = OptimizeRequest(base_players=["8-MID", "8-FWD"])
    result = await optimize_lineup(core, "alice", req)
    
    assert len(result["lineup"]) == 11
    
    squad_ids = [p["id"] for p in result["lineup"] + result["substitutes"]]
    assert "8-MID" in squad_ids
    assert "8-FWD" in squad_ids
