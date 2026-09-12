import json
from pathlib import Path
from typing import Any

_RESOURCES: dict[str, dict[str, Any]] = {}
_RESOURCE_LIST: list[dict[str, Any]] = []


def load_resources(path: Path | None = None) -> None:
    global _RESOURCES, _RESOURCE_LIST
    if path is None:
        path = Path(__file__).resolve().parents[3] / "data" / "resources.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _RESOURCE_LIST = data
    _RESOURCES = {r["resource_id"]: r for r in data}


def get_resource(resource_id: str | None) -> dict[str, Any] | None:
    if not resource_id:
        return None
    return _RESOURCES.get(resource_id)


def all_resources() -> list[dict[str, Any]]:
    return _RESOURCE_LIST


def resource_ids() -> list[str]:
    return list(_RESOURCES.keys())