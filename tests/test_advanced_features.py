import json
import asyncio

import pytest
from fastapi.testclient import TestClient

from src.app.import_catalog import import_catalog
from src.app.core import core
from src.app.database import get_connection, get_players_from_db, get_selections_from_db, save_players_to_db
from src.app.main import app
from src.app.pricing import fixed_price_from_record, normalize_price_millions
from src.app.security import lineup_confirmation_token


client = TestClient(app)


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        (12_000_000, "euros", 12.0),
        (12_000, "thousands", 12.0),
        (12, "millions", 12.0),
        (12_000_000, "auto", 12.0),
        (12_000, "auto", 12.0),
        ("12,5", "millions", 12.5),
    ],
)
def test_price_normalization_supports_declared_units(raw, unit, expected):
    assert normalize_price_millions(raw, unit) == expected


def test_dynamic_market_value_is_never_used_as_fixed_price():
    with pytest.raises(ValueError, match="fixed-price"):
        fixed_price_from_record({"price": 99_000_000, "marketValue": 88_000_000})


def test_team_filter_accepts_accents_and_curacao_alias():
    save_players_to_db([{
        "id": "curacao-player",
        "name": "Alias Player",
        "slug": "alias-player",
        "position": "MID",
        "team": "Curazao",
        "fixed_price": 10.0,
        "fixed_price_source": "test",
        "market_value": 0.0,
        "points": 0,
        "status": "ok",
    }])

    for team_filter in ("Curazao", "Curacao", "Curaçao"):
        players = get_players_from_db(team=team_filter)
        assert any(player["id"] == "curacao-player" for player in players)


def test_team_filter_is_exact_and_selections_expose_filter_values():
    save_players_to_db([
        {
            "id": "exact-spain",
            "name": "Spain Player",
            "slug": None,
            "position": "MID",
            "team": "España",
            "fixed_price": 10.0,
            "fixed_price_source": "test",
            "market_value": 0.0,
            "points": 0,
            "status": "ok",
        },
        {
            "id": "exact-equatorial-guinea",
            "name": "Guinea Player",
            "slug": None,
            "position": "MID",
            "team": "Guinea Ecuatorial",
            "fixed_price": 10.0,
            "fixed_price_source": "test",
            "market_value": 0.0,
            "points": 0,
            "status": "ok",
        },
    ])

    assert any(player["id"] == "exact-spain" for player in get_players_from_db(team="espana"))
    assert not get_players_from_db(team="Esp")
    selections = get_selections_from_db()
    assert {item["name"] for item in selections} >= {"España", "Guinea Ecuatorial"}

    data = asyncio.run(core.get_selections())
    assert data["filter_parameter"] == "team"
    assert data["total"] == len(data["selections"])
    spain = next(item for item in data["selections"] if item["name"] == "España")
    assert spain["filter_value"] == "España"
    assert spain["players_query"] == "?team=Espa%C3%B1a&active_only=false&score_system=<score_system>"


def test_scoring_system_selects_official_points_column():
    save_players_to_db([{
        "id": "scoring-player",
        "name": "Scoring Player",
        "slug": None,
        "position": "FWD",
        "team": "España",
        "fixed_price": 12.0,
        "fixed_price_source": "test",
        "market_value": 0.0,
        "points": 20,
        "points_as": 10,
        "points_sofascore": 20,
        "points_average": 15,
        "points_statistics": 30,
        "status": "ok",
    }])

    expected = {"diario_as": 10, "sofascore": 20, "average": 15, "statistics": 30}
    for score_system, points in expected.items():
        player = next(
            item for item in get_players_from_db(score_system=score_system, limit=10000)
            if item["id"] == "scoring-player"
        )
        assert player["points"] == points
        assert player["points_by_system"] == expected

    data = asyncio.run(core.get_scoring_systems())
    assert data["default"] == "sofascore"
    assert [item["value"] for item in data["values"]] == list(expected)


def test_catalog_import_rejects_invalid_rows_atomically(tmp_path):
    catalog = tmp_path / "players.json"
    catalog.write_text(json.dumps([
        {"id": "1", "name": "Valid", "position": "GK", "team": "A", "fixed_price": 5},
        {"id": "2", "name": "No price", "position": "DEF", "team": "B"},
    ]), encoding="utf-8")

    with pytest.raises(ValueError, match="Catalog rejected"):
        import_catalog(catalog, "millions")


def test_api_requires_key_and_reports_fixed_price_mode():
    assert client.get("/api/users/alice/status").status_code == 401

    response = client.get("/api/users/alice/status", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_write_actions_require_confirmation_but_are_enabled():
    response = client.post(
        "/api/users/alice/actions/make-bid",
        headers={"X-API-Key": "test_key"},
        json={"payload": {"player_id": "1", "amount": 10_000_000}},
    )
    assert response.status_code == 200
    assert response.json()["error"] == "WRITE_CONFIRMATION_REQUIRED"
    assert response.json()["details"]["expected"] == "CONFIRM_BID_alice_1_10000000"


def test_missing_player_is_not_invented():
    response = client.get(
        "/api/users/alice/player/does-not-exist",
        headers={"X-API-Key": "test_key"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == "PLAYER_NOT_FOUND"
    assert response.json()["needs_review"] is True


def test_lineup_confirmation_token_is_deterministic():
    first = lineup_confirmation_token("alice", {"captain": "2", "players": ["1", "2"]})
    second = lineup_confirmation_token("alice", {"players": ["1", "2"], "captain": "2"})
    assert first == second
    assert first.startswith("CONFIRM_LINEUP_")
    assert first != lineup_confirmation_token("bob", {"players": ["1", "2"], "captain": "2"})


def test_user_id_is_required_and_validated():
    headers = {"X-API-Key": "test_key"}
    assert client.get("/api/nonexistent-route", headers=headers).status_code == 404
    assert client.get("/api/users/bad%20user/status", headers=headers).status_code == 422


def test_api_settings_are_isolated_per_user():
    headers = {"X-API-Key": "test_key"}
    alice = client.post(
        "/api/users/alice/settings",
        headers=headers,
        json={"squad_size": 12, "total_budget": 900, "score_system": "average"},
    )
    bob = client.post(
        "/api/users/bob/settings",
        headers=headers,
        json={"squad_size": 15, "total_budget": 1100},
    )
    assert alice.json()["data"]["squad_size"] == 12
    assert bob.json()["data"]["squad_size"] == 15
    assert client.get("/api/users/alice/settings", headers=headers).json()["data"]["total_budget"] == 900
    assert client.get("/api/users/alice/settings", headers=headers).json()["data"]["score_system"] == "average"
    assert client.get("/api/users/bob/settings", headers=headers).json()["data"]["score_system"] == "sofascore"


@pytest.mark.asyncio
async def test_sync_stores_players_matches_standings_and_reports(monkeypatch):
    with get_connection() as conn:
        for table in ("player_reports", "standings", "matches", "players"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self.payload = payload
            self.status_code = status_code

        def json(self):
            return self.payload

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, timeout=None):
            if "/players/" in url:
                return FakeResponse({"data": {"reports": [{
                    "points": {"2": 7},
                    "rawStats": {"goals": 1, "assists": 1, "minutesPlayed": 90, "sofascore": 8.2},
                    "match": {
                        "id": 501,
                        "status": "finished",
                        "date": 1_800_000_000,
                        "round": {"name": "Grupo A"},
                        "home": {"name": "Spain", "score": 2},
                        "away": {"name": "France", "score": 1},
                    },
                }]}})
            if "/standings" in url:
                return FakeResponse({"data": {
                    "name": "Grupo A",
                    "teams": [{
                        "team": {"id": 10, "name": "Spain"},
                        "position": 1, "points": 3, "won": 1, "lost": 0,
                        "tied": 0, "scored": 2, "against": 1,
                    }],
                }})
            if "round=1" in url:
                return FakeResponse({"data": {"activeEvents": [{"games": [{
                    "id": 501,
                    "date": 1_800_000_000,
                    "status": "finished",
                    "home": {"id": 10, "name": "Spain", "score": 2},
                    "away": {"id": 20, "name": "France", "score": 1},
                }]}]}})
            score_points = {"score=1": 11, "score=2": 22, "score=3": 33, "score=4": 44}
            points = next((value for marker, value in score_points.items() if marker in url), 7)
            return FakeResponse({"data": {
                "teams": {"10": {"name": "Spain"}},
                "players": {"p1": {
                    "name": "Jugador Uno", "slug": "jugador-uno", "position": 3,
                    "teamID": 10, "fantasyPrice": 9_500_000, "price": 99_000_000,
                    "points": points, "status": "ok",
                }},
                "activeEvents": [{
                    "name": "Grupo A",
                    "games": [{
                        "id": 501,
                        "date": 1_800_000_000,
                        "status": "finished",
                        "home": {"id": 10, "name": "Spain", "score": 2},
                        "away": {"id": 20, "name": "France", "score": 1},
                    }],
                }],
                "season": {"rounds": [{"id": 1, "name": "Grupo A"}]},
            }})

    monkeypatch.setattr("src.app.core.httpx.AsyncClient", FakeAsyncClient)
    result = await core.sync_database()

    assert result["synchronized"] is True
    assert result["synchronized_at"].endswith("+00:00")
    assert result["players_count"] == 1
    assert result["matches_count"] == 1
    assert result["standings_count"] == 1
    with get_connection() as conn:
        player = conn.execute(
            """SELECT fixed_price, market_value, goals, assists, points_as,
                      points_sofascore, points_average, points_statistics
               FROM players WHERE id = 'p1'"""
        ).fetchone()
        assert tuple(player) == (9.5, 99.0, 1, 1, 11, 22, 33, 44)
        assert conn.execute("SELECT count(*) FROM matches WHERE id = 501").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM standings WHERE team_id = 10").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM player_reports WHERE player_id = 'p1'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_case_and_accent_insensitive_search():
    from src.app.database import save_players_to_db, get_players_from_db
    save_players_to_db([
        {
            "id": "test-p1",
            "name": "Iago Aspas",
            "slug": None,
            "position": "FWD",
            "team": "España",
            "fixed_price": 12.0,
            "fixed_price_source": "test",
            "market_value": 12.0,
            "points": 50,
            "status": "ok"
        },
        {
            "id": "test-p2",
            "name": "De Bruyne",
            "slug": None,
            "position": "MID",
            "team": "Bélgica",
            "fixed_price": 20.0,
            "fixed_price_source": "test",
            "market_value": 20.0,
            "points": 80,
            "status": "ok"
        }
    ])
    
    # Test lowercase espanas
    players_es1 = get_players_from_db(team="espana")
    assert len(players_es1) >= 1
    assert any(p["id"] == "test-p1" for p in players_es1)

    # Test accents and uppercase
    players_es2 = get_players_from_db(team="ESPAÑA")
    assert len(players_es2) >= 1
    
    # Partial names are intentionally rejected to avoid ambiguous selections.
    players_es3 = get_players_from_db(team="Esp")
    assert players_es3 == []

    # Test accents removal
    players_be = get_players_from_db(team="belgica")
    assert len(players_be) >= 1
    assert any(p["id"] == "test-p2" for p in players_be)


@pytest.mark.asyncio
async def test_local_squad_storage_and_simulated_transactions():
    from src.app.database import save_players_to_db, get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM user_squad_players WHERE user_id = 'charlie'")
        conn.commit()

    players_list = []
    player_ids = [f"s-{i}" for i in range(1, 13)] # 12 players
    for pid in player_ids:
        players_list.append({
            "id": pid,
            "name": f"Player {pid}",
            "slug": None,
            "position": "GK" if pid == "s-1" else "DEF" if pid == "s-2" else "MID",
            "team": "Spain",
            "fixed_price": 5.0 if pid == "s-1" else 10.0,
            "fixed_price_source": "test",
            "market_value": 10.0,
            "points": 10,
            "status": "ok"
        })
    save_players_to_db(players_list)
    
    # Set lineup with 11 players
    headers = {"X-API-Key": "test_key"}
    payload = {
        "players": player_ids[:11],
        "captain_id": "s-2"
    }
    token = lineup_confirmation_token("charlie", payload)
    response = client.post(
        "/api/users/charlie/actions/set-lineup",
        headers=headers,
        json={
            "payload": payload,
            "confirmation_token": token
        }
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    
    # Get team
    team_resp = client.get("/api/users/charlie/team", headers=headers)
    assert team_resp.status_code == 200
    team_data = team_resp.json()["data"]
    assert len(team_data["players"]) == 11
    # Total spent: 5.0 (for s-1) + 10 * 10.0 = 105.0
    assert team_data["balance"] == 920.0 - 105.0
    
    # Simulated bid (make_bid) adds player s-12
    bid_resp = client.post(
        "/api/users/charlie/actions/make-bid",
        headers=headers,
        json={"payload": {"player_id": "s-12", "amount": 1000000}, "confirmation_token": "CONFIRM_BID_charlie_s-12_1000000"}
    )
    assert bid_resp.status_code == 200
    assert bid_resp.json()["ok"] is True
    
    # Verify s-12 is in squad
    team_resp2 = client.get("/api/users/charlie/team", headers=headers)
    assert len(team_resp2.json()["data"]["players"]) == 12
    
    # Simulated sale (sell_player) removes s-1
    sell_resp = client.post(
        "/api/users/charlie/actions/sell-player",
        headers=headers,
        json={"payload": {"player_id": "s-1"}, "confirmation_token": "CONFIRM_SELL_charlie_s-1"}
    )
    assert sell_resp.status_code == 200
    assert sell_resp.json()["ok"] is True
    
    # Verify s-1 is removed
    team_resp3 = client.get("/api/users/charlie/team", headers=headers)
    assert len(team_resp3.json()["data"]["players"]) == 11
    assert not any(p["id"] == "s-1" for p in team_resp3.json()["data"]["players"])


@pytest.mark.asyncio
async def test_read_only_api_key_restrictions(monkeypatch):
    from src.app.config import settings
    monkeypatch.setattr(settings, "read_only_api_key", "restricted_key")

    # Read-only keys can use stateless public routes.
    response_get = client.get(
        "/api/status",
        headers={"X-API-Key": "restricted_key"}
    )
    assert response_get.status_code == 200
    assert response_get.json()["ok"] is True

    # Private user workspaces require the admin key.
    response_private = client.get(
        "/api/users/charlie/status",
        headers={"X-API-Key": "restricted_key"}
    )
    assert response_private.status_code == 403
    assert response_private.json()["detail"]["error"] == "WRITE_ACCESS_DENIED"

    # POST to actions should return 403 Forbidden
    response_post = client.post(
        "/api/users/charlie/actions/make-bid",
        headers={"X-API-Key": "restricted_key"},
        json={"payload": {"player_id": "s-1", "amount": 1000000}}
    )
    assert response_post.status_code == 403
    assert response_post.json()["detail"]["error"] == "WRITE_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_public_get_endpoints_without_user():
    headers = {"X-API-Key": "test_key"}
    
    # Test public status
    resp_status = client.get("/api/status", headers=headers)
    assert resp_status.status_code == 200
    assert resp_status.json()["ok"] is True
    assert "interface_mode" in resp_status.json()["data"]
    
    # Test public players
    missing_score = client.get("/api/players", headers=headers)
    assert missing_score.json()["error"] == "SCORING_SYSTEM_REQUIRED"

    resp_players = client.get("/api/players", headers=headers, params={"score_system": "sofascore"})
    assert resp_players.status_code == 200
    assert resp_players.json()["ok"] is True

    resp_selections = client.get("/api/selections", headers=headers)
    assert resp_selections.status_code == 200
    assert resp_selections.json()["data"]["filter_parameter"] == "team"

    resp_scoring = client.get("/api/scoring-systems", headers=headers)
    assert resp_scoring.status_code == 200
    assert len(resp_scoring.json()["data"]["values"]) == 4
    
    # Test public matches
    resp_matches = client.get("/api/matches", headers=headers)
    assert resp_matches.status_code == 200
    assert resp_matches.json()["ok"] is True


@pytest.mark.asyncio
async def test_public_stateless_computations(monkeypatch):
    from src.app.config import settings
    monkeypatch.setattr(settings, "read_only_api_key", "restricted_key")
    
    # We will test both with admin and read-only keys
    for key in ["test_key", "restricted_key"]:
        headers = {"X-API-Key": key}
        
        # 1. Test public optimize-lineup (using PublicOptimizeRequest)
        opt_resp = client.post(
            "/api/optimize-lineup",
            headers=headers,
            json={
                "phase": "groups",
                "objective": "points_and_value",
                "budget": 920.0,
                "formation": "4-4-2",
                "squad_size": 11,
                "score_system": "sofascore",
            }
        )
        assert opt_resp.status_code == 200
        assert opt_resp.json()["ok"] is True
        
        # 2. Test public build-phase-plan
        plan_resp = client.post(
            "/api/build-phase-plan",
            headers=headers,
            json={"phase": "quarter_finals", "target_budget": 1040.0}
        )
        assert plan_resp.status_code == 200
        assert plan_resp.json()["ok"] is True
        
        # 3. Test public suggest-alternatives (stateless using default user catalog)
        # Note: We use s-2 which is seeded in the test catalog
        alt_resp = client.post(
            "/api/suggest-alternatives",
            headers=headers,
            json={"player_id": "s-2", "max_price": 50.0, "score_system": "sofascore"}
        )
        assert alt_resp.status_code == 200
        assert alt_resp.json()["ok"] is True
        
        # 4. Test public pick-captain & pick-ariete
        cap_resp = client.post(
            "/api/pick-captain",
            headers=headers,
            json={"phase": "groups", "score_system": "sofascore"}
        )
        assert cap_resp.status_code == 200
        assert cap_resp.json()["ok"] is True
        
        ariete_resp = client.post(
            "/api/pick-ariete",
            headers=headers,
            json={"phase": "groups", "score_system": "sofascore"}
        )
        assert ariete_resp.status_code == 200
        assert ariete_resp.json()["ok"] is True
        
        # 5. Test public compare-players
        comp_resp = client.post(
            "/api/compare-players",
            headers=headers,
            json={"player_ids": ["s-2", "s-3"], "score_system": "sofascore"}
        )
        assert comp_resp.status_code == 200
        assert comp_resp.json()["ok"] is True
