"""Export the authenticated tenant API as the MyGPT Actions contract."""

from pathlib import Path
from copy import deepcopy
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.app.main import app  # noqa: E402
from src.app.config import settings  # noqa: E402


OUTPUT = ROOT / "openapi" / "mygpt_openapi_minimal.yaml"

QUERY_PARAMETER_ENUMS = {
    ("players", "position"): ["GK", "DEF", "MID", "FWD"],
    ("players", "status"): ["ok", "doubtful", "injured", "suspended", "no_disponible"],
    ("players", "sort_by"): ["fixed_price", "points", "market_value", "goals", "assists", "name", "team"],
    ("matches", "status"): ["pending", "preview", "finished"],
}


def normalize_action_parameters(schema: dict) -> None:
    for path, path_item in schema["paths"].items():
        resource = path.rstrip("/").split("/")[-1]
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("name") == "user_id" and parameter.get("in") == "path":
                    parameter["description"] = "Stable tenant identifier; this is not authentication."
                    parameter["schema"]["pattern"] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
                    parameter["schema"]["maxLength"] = 64
                    continue
                if parameter.get("in") != "query":
                    continue
                parameter["required"] = False
                parameter["style"] = "form"
                parameter["explode"] = True
                parameter_schema = parameter.get("schema", {})
                any_of = parameter_schema.pop("anyOf", None)
                if any_of:
                    non_null = next((item for item in any_of if item.get("type") != "null"), {})
                    parameter_schema.update(non_null)
                enum = QUERY_PARAMETER_ENUMS.get((resource, parameter.get("name")))
                if enum:
                    parameter_schema["enum"] = enum


def build_contract() -> dict:
    schema = deepcopy(app.openapi())
    schema["info"]["title"] = "Biwenger Mundial 2026 Actions API"
    schema["info"]["description"] = (
        "API privada de administración del optimizador Biwenger Mundial 2026. "
        "Requiere la clave API de administrador en X-API-Key y un user_id estable en cada ruta. "
        "user_id separa configuración, ratings y plantilla local, pero no autentica por sí solo. "
        "Las acciones de plantilla son simulaciones locales y requieren el confirmation_token devuelto por la API. "
        "No existe acceso a credenciales ni a la cuenta real de Biwenger."
    )
    schema["servers"] = [{"url": settings.api_public_url.rstrip("/")}]
    schema["paths"] = {
        path: definition
        for path, definition in schema["paths"].items()
        if path.startswith("/api/users/{user_id}/")
    }
    normalize_action_parameters(schema)
    return schema


def build_public_readonly_contract() -> dict:
    schema = deepcopy(app.openapi())
    schema["info"]["title"] = "Biwenger Mundial 2026 Public API (Read-Only)"
    schema["info"]["description"] = (
        "API pública de precios fijos para consultar y calcular sobre el catálogo del Mundial 2026. "
        "Las operaciones no modifican datos y requieren X-API-Key."
    )
    schema["servers"] = [{"url": settings.api_public_url.rstrip("/")}]
    
    public_paths = {}
    for path, path_item in schema["paths"].items():
        if path.startswith("/api/") and not path.startswith("/api/users/"):
            operations = {
                method: operation
                for method, operation in path_item.items()
                if method in {"get", "post"}
            }
            if operations:
                public_paths[path] = operations
                
    schema["paths"] = public_paths

    normalize_action_parameters(schema)
    return schema


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        yaml.safe_dump(build_contract(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")

    public_output = OUTPUT.parent / "mygpt_openapi_public_readonly.yaml"
    public_output.write_text(
        yaml.safe_dump(build_public_readonly_contract(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {public_output}")


if __name__ == "__main__":
    main()
