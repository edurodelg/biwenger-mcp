from typing import Literal


ScoringSystem = Literal["diario_as", "sofascore", "average", "statistics"]

DEFAULT_SCORING_SYSTEM: ScoringSystem = "sofascore"

SCORING_SYSTEMS = {
    "diario_as": {
        "source_id": 1,
        "name": "Diario AS",
        "kind": "as",
        "points_column": "points_as",
    },
    "sofascore": {
        "source_id": 2,
        "name": "SofaScore",
        "kind": "SofaScore",
        "points_column": "points_sofascore",
    },
    "average": {
        "source_id": 3,
        "name": "Media AS y SofaScore",
        "kind": "Average",
        "points_column": "points_average",
    },
    "statistics": {
        "source_id": 4,
        "name": "Estadísticas",
        "kind": "Stats",
        "points_column": "points_statistics",
    },
}


def scoring_system_catalog() -> dict:
    return {
        "filter_parameter": "score_system",
        "default": DEFAULT_SCORING_SYSTEM,
        "values": [
            {
                "value": value,
                "source_id": details["source_id"],
                "name": details["name"],
                "kind": details["kind"],
            }
            for value, details in SCORING_SYSTEMS.items()
        ],
        "applies_to": [
            "players",
            "player",
            "compare-players",
            "optimize-lineup",
            "pick-captain",
            "pick-ariete",
            "suggest-alternatives",
        ],
    }
