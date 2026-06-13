from .config import settings


PHASES = ("groups", "round_of_16", "quarter_finals", "semi_finals", "final")
ALLOWED_FORMATIONS = ("3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1")
PARTIAL_STANDINGS_WARNING = (
    "Biwenger currently publishes only a partial group-stage table; "
    "missing groups are not inferred. Recommendations using standings require review."
)


def standings_warnings(standings: list[dict]) -> list[str]:
    return [PARTIAL_STANDINGS_WARNING] if 0 < len(standings) < 48 else []


def _millions(value: int | float) -> float:
    return value / 1_000_000.0 if value >= 1_000_000 else float(value)


def get_phase_rules(phase: str) -> dict:
    """Return the configured private-league example rules for one phase."""
    if phase not in PHASES:
        phase = "groups"
    phase_index = PHASES.index(phase)
    max_same_team = {
        "groups": settings.group_max_players_same_team,
        "round_of_16": settings.round_of_16_max_players_same_team,
        "quarter_finals": settings.quarter_final_max_players_same_team,
        "semi_finals": settings.semifinal_max_players_same_team,
        "final": settings.final_max_players_same_team,
    }[phase]
    transfers = {
        "groups": settings.group_transfers,
        "round_of_16": settings.round_of_16_transfers,
        "quarter_finals": settings.quarter_final_transfers,
        "semi_finals": settings.semifinal_transfers,
        "final": settings.final_transfers,
    }[phase]
    return {
        "phase": phase,
        "budget": _millions(settings.group_budget + phase_index * settings.budget_increment_per_phase),
        "max_players_same_team": max_same_team,
        "transfers_allowed": transfers,
        "captain_max_price": _millions(settings.captain_max_price + phase_index * settings.captain_price_increment_per_phase),
        "ariete_max_price": _millions(settings.ariete_max_price + phase_index * settings.ariete_price_increment_per_phase),
    }


def get_example_ruleset() -> dict:
    return {
        "name": "Ejemplo de configuración de una liga privada",
        "is_example": True,
        "disclaimer": "Estas cifras son un ejemplo configurable usado por los autores; no son reglas oficiales de Biwenger.",
        "price_mode": "fixed_only",
        "price_unit": "millions",
        "phases": {phase: get_phase_rules(phase) for phase in PHASES},
        "prizes": {
            "purpose": "Cena o comida de la liga",
            "venue_choice": "El lugar lo elige la persona ganadora",
            "amounts_eur": settings.prizes_eur,
            "negative_amount_meaning": "Las posiciones 4, 5 y 6 aportan esa cantidad a la cena o comida.",
        },
    }
