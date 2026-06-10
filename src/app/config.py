import json
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Load optional local configuration. config.yaml and config.json are ignored by Git.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
json_path = ROOT_DIR / "config.json"
yaml_path = ROOT_DIR / "config.yaml"

config_data = {}
if yaml_path.exists():
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
    except Exception as e:
        import logging
        logging.getLogger("Config").warning(f"Failed to load config.yaml: {e}")
elif json_path.exists():
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            config_data = json.load(f) or {}
    except Exception as e:
        import logging
        logging.getLogger("Config").warning(f"Failed to load config.json: {e}")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    interface_mode: str = Field(default=config_data.get("INTERFACE_MODE", "api"), alias="INTERFACE_MODE")
    api_key: str = Field(default=config_data.get("API_KEY", "change_me_long_random_key"), alias="API_KEY")
    read_only_api_key: str | None = Field(default=config_data.get("READ_ONLY_API_KEY", None), alias="READ_ONLY_API_KEY")
    api_public_url: str = Field(
        default=config_data.get("API_PUBLIC_URL", "https://biwenger-mcp.cestmail.com"),
        alias="API_PUBLIC_URL",
    )
    database_url: str = Field(default=config_data.get("DATABASE_URL", "sqlite:///data/biwenger.db"), alias="DATABASE_URL")
    biwenger_competition_slug: str = Field(
        default=config_data.get("BIWENGER_COMPETITION_SLUG", "world-cup"),
        alias="BIWENGER_COMPETITION_SLUG",
    )

    require_write_confirmation: bool = Field(default=config_data.get("REQUIRE_WRITE_CONFIRMATION", True), alias="REQUIRE_WRITE_CONFIRMATION")
    default_squad_size: int = Field(default=config_data.get("DEFAULT_SQUAD_SIZE", 11), alias="DEFAULT_SQUAD_SIZE")
    default_user_id: str = Field(default=config_data.get("DEFAULT_USER_ID", "default_user"), alias="DEFAULT_USER_ID")
    enable_daily_sync: bool = Field(default=config_data.get("ENABLE_DAILY_SYNC", True), alias="ENABLE_DAILY_SYNC")

    captain_max_price: int = Field(default=config_data.get("CAPTAIN_MAX_PRICE", 70_000_000), alias="CAPTAIN_MAX_PRICE")
    captain_price_increment_per_phase: int = Field(default=config_data.get("CAPTAIN_PRICE_INCREMENT_PER_PHASE", 15_000_000), alias="CAPTAIN_PRICE_INCREMENT_PER_PHASE")
    ariete_max_price: int = Field(default=config_data.get("ARIETE_MAX_PRICE", 90_000_000), alias="ARIETE_MAX_PRICE")
    ariete_price_increment_per_phase: int = Field(default=config_data.get("ARIETE_PRICE_INCREMENT_PER_PHASE", 15_000_000), alias="ARIETE_PRICE_INCREMENT_PER_PHASE")

    group_budget: int = Field(default=config_data.get("GROUP_BUDGET", 920_000_000), alias="GROUP_BUDGET")
    budget_increment_per_phase: int = Field(default=config_data.get("BUDGET_INCREMENT_PER_PHASE", 60_000_000), alias="BUDGET_INCREMENT_PER_PHASE")

    group_max_players_same_team: int = Field(default=config_data.get("GROUP_MAX_PLAYERS_SAME_TEAM", 3), alias="GROUP_MAX_PLAYERS_SAME_TEAM")
    round_of_16_max_players_same_team: int = Field(default=config_data.get("ROUND_OF_16_MAX_PLAYERS_SAME_TEAM", 3), alias="ROUND_OF_16_MAX_PLAYERS_SAME_TEAM")
    quarter_final_max_players_same_team: int = Field(default=config_data.get("QUARTER_FINAL_MAX_PLAYERS_SAME_TEAM", 4), alias="QUARTER_FINAL_MAX_PLAYERS_SAME_TEAM")
    semifinal_max_players_same_team: int = Field(default=config_data.get("SEMIFINAL_MAX_PLAYERS_SAME_TEAM", 5), alias="SEMIFINAL_MAX_PLAYERS_SAME_TEAM")
    final_max_players_same_team: int = Field(default=config_data.get("FINAL_MAX_PLAYERS_SAME_TEAM", 6), alias="FINAL_MAX_PLAYERS_SAME_TEAM")

    group_transfers: int = Field(default=config_data.get("GROUP_TRANSFERS", 3), alias="GROUP_TRANSFERS")
    round_of_16_transfers: int = Field(default=config_data.get("ROUND_OF_16_TRANSFERS", 4), alias="ROUND_OF_16_TRANSFERS")
    quarter_final_transfers: int = Field(default=config_data.get("QUARTER_FINAL_TRANSFERS", 4), alias="QUARTER_FINAL_TRANSFERS")
    semifinal_transfers: int = Field(default=config_data.get("SEMIFINAL_TRANSFERS", 5), alias="SEMIFINAL_TRANSFERS")
    final_transfers: int = Field(default=config_data.get("FINAL_TRANSFERS", 6), alias="FINAL_TRANSFERS")

    prizes_eur: dict[str, int] = Field(
        default=config_data.get("PRIZES_EUR", {"1": 30, "2": 20, "3": 10, "4": -10, "5": -20, "6": -30}),
        alias="PRIZES_EUR",
    )

settings = Settings()
