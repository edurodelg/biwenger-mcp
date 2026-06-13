from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .database import init_db, save_players_to_db
from .pricing import PriceUnit, normalize_price_millions


POSITION_ALIASES = {
    "1": "GK", "GK": "GK", "POR": "GK", "PORTERO": "GK",
    "2": "DEF", "DEF": "DEF", "DF": "DEF", "DEFENSA": "DEF",
    "3": "MID", "MID": "MID", "MC": "MID", "MEDIO": "MID", "CENTROCAMPISTA": "MID",
    "4": "FWD", "FWD": "FWD", "DEL": "FWD", "DL": "FWD", "DELANTERO": "FWD",
}


def normalize_catalog_record(record: dict, price_unit: PriceUnit) -> dict:
    player_id = str(record.get("id") or record.get("player_id") or "").strip()
    name = str(record.get("name") or record.get("player_name") or "").strip()
    team = str(record.get("team") or record.get("country") or "").strip()
    position_raw = str(record.get("position") or record.get("pos") or "").strip().upper()
    position = POSITION_ALIASES.get(position_raw)
    raw_price = record.get("fixed_price")
    if raw_price in (None, ""):
        raw_price = record.get("fantasyPrice")
    if raw_price in (None, ""):
        raw_price = record.get("fixedPrice")

    missing = [label for label, value in (("id", player_id), ("name", name), ("team", team), ("position", position)) if not value]
    if missing:
        raise ValueError(f"Missing or invalid fields: {', '.join(missing)}.")

    return {
        "id": player_id,
        "name": name,
        "slug": str(record.get("slug") or "").strip() or None,
        "position": position,
        "team": team,
        "fixed_price": normalize_price_millions(raw_price, price_unit),
        "fixed_price_source": f"catalog:{price_unit}",
        "market_value": 0.0,
        "points": int(float(record.get("points") or 0)),
        "status": str(record.get("status") or "ok").strip().lower(),
    }


def load_catalog(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload = payload.get("players") or payload.get("data")
        if not isinstance(payload, list):
            raise ValueError("JSON catalog must be a list or contain a 'players' list.")
        return payload
    raise ValueError("Catalog must be a .json or .csv file.")


def import_catalog(path: Path, price_unit: PriceUnit = "auto") -> dict:
    records = load_catalog(path)
    players = []
    errors = []
    for index, record in enumerate(records, start=1):
        try:
            players.append(normalize_catalog_record(record, price_unit))
        except (TypeError, ValueError) as exc:
            errors.append(f"row {index}: {exc}")

    if errors:
        preview = "; ".join(errors[:10])
        raise ValueError(f"Catalog rejected ({len(errors)} invalid rows): {preview}")
    if not players:
        raise ValueError("Catalog contains no players.")

    init_db()
    save_players_to_db(players)
    return {"imported": len(players), "source": str(path), "price_unit": price_unit}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a fixed-price Biwenger player catalog into SQLite.")
    parser.add_argument("catalog", type=Path, help="CSV or JSON catalog path")
    parser.add_argument(
        "--price-unit",
        choices=("auto", "euros", "thousands", "millions"),
        default="auto",
        help="Unit used by fixed_price/fantasyPrice/fixedPrice (default: auto)",
    )
    args = parser.parse_args()
    result = import_catalog(args.catalog, args.price_unit)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
