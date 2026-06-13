from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from scripts.export_mygpt_openapi import build_contract, build_public_readonly_contract
from src.app.core import core
from src.app.database import get_connection, init_db, save_players_to_db
from src.app.main import app


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "openapi" / "mygpt_openapi_minimal.yaml"
PUBLIC_CONTRACT_PATH = ROOT / "openapi" / "mygpt_openapi_public_readonly.yaml"
HEADERS = {"X-API-Key": "test_key"}
USER_PATH = "/api/users/mygpt-test"
client = TestClient(app)


def seed_catalog() -> None:
    init_db()
    with get_connection() as conn:
        for table in ("user_player_ratings", "user_settings", "player_reports", "standings", "matches", "players"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

    players = []
    for team_index in range(1, 9):
        for position_index, position in enumerate(("GK", "DEF", "MID", "FWD"), start=1):
            players.append({
                "id": f"{team_index}-{position}",
                "name": f"Player {team_index} {position}",
                "slug": None,
                "position": position,
                "team": f"Team {team_index}",
                "fixed_price": 4.0 + team_index + position_index / 10,
                "fixed_price_source": "contract-test",
                "market_value": 0.0,
                "points": 100 - team_index - position_index,
                "status": "ok",
            })
    save_players_to_db(players)


def contract_operations(contract: dict) -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete"}
    return {
        (method.upper(), path)
        for path, path_item in contract["paths"].items()
        for method in path_item
        if method in methods
    }


def test_mygpt_contract_is_exact_export_of_fastapi():
    published = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    generated = build_contract()

    assert published == generated
    assert published["servers"] == [{"url": "https://biwenger-mcp.cestmail.com"}]
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    operation_ids = []
    for path, path_item in published["paths"].items():
        assert path.startswith("/api/users/{user_id}/")
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_ids.append(operation["operationId"])
            assert operation["security"] == [{"APIKeyHeader": []}]
            user_parameter = next(p for p in operation["parameters"] if p["name"] == "user_id")
            assert user_parameter["required"] is True
            assert user_parameter["schema"]["maxLength"] == 64

    assert len(operation_ids) == len(set(operation_ids))
    assert len(operation_ids) == 27

    matches_parameters = {
        parameter["name"]: parameter
        for parameter in published["paths"]["/api/users/{user_id}/matches"]["get"]["parameters"]
    }
    assert matches_parameters["status"]["schema"]["enum"] == ["pending", "preview", "finished", "injuryTime"]
    assert "anyOf" not in matches_parameters["round_name"]["schema"]
    assert "style" not in matches_parameters["status"]
    assert "explode" not in matches_parameters["status"]


def test_public_contract_has_all_stateless_operations_and_stable_query_parameters():
    published = yaml.safe_load(PUBLIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    generated = build_public_readonly_contract()

    assert published == generated
    assert published["paths"]
    assert contract_operations(published) == {
        ("GET", "/api/status"),
        ("GET", "/api/rules"),
        ("GET", "/api/players"),
        ("GET", "/api/selections"),
        ("GET", "/api/scoring-systems"),
        ("GET", "/api/player/{player_id}"),
        ("GET", "/api/matches"),
        ("GET", "/api/standings"),
        ("GET", "/api/leagues"),
        ("POST", "/api/optimize-lineup"),
        ("POST", "/api/suggest-alternatives"),
        ("POST", "/api/pick-captain"),
        ("POST", "/api/pick-ariete"),
        ("POST", "/api/compare-players"),
        ("POST", "/api/build-phase-plan"),
    }

    players_parameters = {
        parameter["name"]: parameter
        for parameter in published["paths"]["/api/players"]["get"]["parameters"]
    }
    assert players_parameters["position"]["schema"]["enum"] == ["GK", "DEF", "MID", "FWD"]
    assert players_parameters["status"]["schema"]["enum"][:3] == ["ok", "doubtful", "warned"]
    assert players_parameters["score_system"]["schema"]["enum"] == [
        "diario_as", "sofascore", "average", "statistics"
    ]
    assert players_parameters["score_system"]["required"] is True
    assert "anyOf" not in players_parameters["team"]["schema"]
    assert "style" not in players_parameters["position"]
    assert "explode" not in players_parameters["position"]

    matches_parameters = {
        parameter["name"]: parameter
        for parameter in published["paths"]["/api/matches"]["get"]["parameters"]
    }
    assert matches_parameters["status"]["schema"]["enum"] == ["pending", "preview", "finished", "injuryTime"]
    assert "anyOf" not in matches_parameters["round_name"]["schema"]

    player_parameters = {
        parameter["name"]: parameter
        for parameter in published["paths"]["/api/player/{player_id}"]["get"]["parameters"]
    }
    assert player_parameters["score_system"]["required"] is True

    scoring_body_operations = {
        "/api/optimize-lineup": "PublicOptimizeRequest",
        "/api/suggest-alternatives": "SuggestAlternativesRequest",
        "/api/pick-captain": "OptimizeRequest",
        "/api/pick-ariete": "OptimizeRequest",
        "/api/compare-players": "ComparePlayersRequest",
    }
    for path, component_name in scoring_body_operations.items():
        operation = published["paths"][path]["post"]
        body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert body_schema["$ref"] == f"#/components/schemas/{component_name}"
        assert "score_system" in published["components"]["schemas"][component_name]["required"]


def test_rules_endpoint_exposes_example_rules_and_prizes():
    response = client.get(f"{USER_PATH}/rules", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_example"] is True
    assert data["price_mode"] == "fixed_only"
    assert data["phases"]["groups"] == {
        "phase": "groups",
        "budget": 920.0,
        "max_players_same_team": 3,
        "transfers_allowed": 3,
        "captain_max_price": 70.0,
        "ariete_max_price": 90.0,
    }
    assert data["phases"]["round_of_16"]["max_players_same_team"] == 3
    assert data["phases"]["quarter_finals"]["max_players_same_team"] == 4
    assert data["prizes"]["amounts_eur"] == {"1": 30, "2": 20, "3": 10, "4": -10, "5": -20, "6": -30}


@pytest.mark.asyncio
async def test_every_mygpt_operation_accepts_a_representative_request(monkeypatch):
    seed_catalog()

    async def fake_sync():
        return {
            "synchronized": True,
            "synchronized_at": "2026-06-10T12:00:00+00:00",
            "players_count": 32,
            "matches_count": 0,
            "standings_count": 0,
            "message": "Test synchronization.",
        }

    monkeypatch.setattr(core, "sync_database", fake_sync)

    calls = [
        ("GET", f"{USER_PATH}/status", None, None),
        ("GET", f"{USER_PATH}/rules", None, None),
        ("GET", f"{USER_PATH}/settings", None, None),
        ("POST", f"{USER_PATH}/settings", {"squad_size": 12, "active_formation": "4-4-2"}, None),
        ("POST", f"{USER_PATH}/sync", None, None),
        ("GET", f"{USER_PATH}/sync", None, None),
        ("GET", f"{USER_PATH}/players", None, {"position": "MID", "limit": 10}),
        ("GET", f"{USER_PATH}/selections", None, None),
        ("GET", f"{USER_PATH}/scoring-systems", None, None),
        ("GET", f"{USER_PATH}/player/1-GK", None, None),
        ("GET", f"{USER_PATH}/matches", None, {"status": "pending"}),
        ("GET", f"{USER_PATH}/standings", None, None),
        ("GET", f"{USER_PATH}/leagues", None, None),
        ("GET", f"{USER_PATH}/team", None, None),
        ("GET", f"{USER_PATH}/market", None, None),
        ("POST", f"{USER_PATH}/player/1-GK/rating", {"manual_rating": 8.5}, None),
        ("POST", f"{USER_PATH}/compare-players", {"player_ids": ["1-GK", "2-GK"]}, None),
        ("POST", f"{USER_PATH}/build-phase-plan", {"phase": "groups"}, None),
        ("POST", f"{USER_PATH}/optimize-lineup", {"phase": "groups"}, None),
        ("POST", f"{USER_PATH}/pick-captain", {"phase": "groups"}, None),
        ("POST", f"{USER_PATH}/pick-ariete", {"phase": "groups"}, None),
        ("POST", f"{USER_PATH}/suggest-alternatives", {"player_id": "1-GK"}, None),
        ("POST", f"{USER_PATH}/actions/set-lineup", {"payload": {"players": [f"{i}-MID" for i in range(1, 9)] + ["1-GK", "1-DEF", "1-FWD"]}}, None),
        ("POST", f"{USER_PATH}/actions/make-bid", {"payload": {"player_id": "1-GK", "amount": 5_000_000}}, None),
        ("POST", f"{USER_PATH}/actions/cancel-bid", {"payload": {"player_id": "1-GK"}}, None),
        ("POST", f"{USER_PATH}/actions/sell-player", {"payload": {"player_id": "1-GK"}}, None),
        ("POST", f"{USER_PATH}/actions/accept-offer", {"payload": {"offer_id": "offer-1"}}, None),
    ]

    exercised = set()
    for method, url, body, params in calls:
        response = client.request(method, url, headers=HEADERS, json=body, params=params)
        assert response.status_code < 500, f"{method} {url}: {response.text}"
        assert response.status_code not in {404, 405, 422}, f"{method} {url}: {response.text}"
        contract_path = url.replace(USER_PATH, "/api/users/{user_id}")
        contract_path = contract_path.replace("/player/1-GK/rating", "/player/{player_id}/rating")
        contract_path = contract_path.replace("/player/1-GK", "/player/{player_id}")
        exercised.add((method, contract_path))

    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert exercised == contract_operations(contract)


def test_mygpt_public_readonly_contract_has_no_user_endpoints():
    from scripts.export_mygpt_openapi import build_public_readonly_contract
    public_path = ROOT / "openapi" / "mygpt_openapi_public_readonly.yaml"
    published = yaml.safe_load(public_path.read_text(encoding="utf-8"))
    generated = build_public_readonly_contract()
    assert published == generated
    
    for path, path_item in published["paths"].items():
        assert "{user_id}" not in path
        assert set(path_item).issubset({"get", "post"})
