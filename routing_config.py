"""Pure configuration validation for advisory routing; no runtime I/O."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

MAX_CHOICE_OPTIONS = 255


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    description: str
    details: str = ""


def catalog(value: Any, *, limit: int) -> tuple[CatalogEntry, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"catalog must be a list with at most {limit} entries")
    entries = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "description", "details"}:
            raise ValueError("invalid catalog entry")
        for key, limit in (("id", 128), ("description", 4096), ("details", 8000)):
            text = item.get(key, "")
            if not isinstance(text, str) or len(text) > limit or (key != "details" and not text.strip()):
                raise ValueError("invalid catalog field")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", item["id"]):
            raise ValueError("invalid catalog identifier")
        entries.append(CatalogEntry(item["id"], item["description"], item.get("details", "")))
    if len({entry.id for entry in entries}) != len(entries):
        raise ValueError("duplicate catalog identifier")
    return tuple(entries)


def parse_routing(data: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, active in (("skills", "suggest"), ("tools", "filter"), ("results", "filter"),
                        ("profiles", "suggest"), ("recovery", "suggest")):
        mode = data.get(f"{key}_mode", "off")
        if mode not in ("off", "observe", active):
            raise ValueError("invalid routing mode")
        result[f"{key}_mode"] = mode
    for key in ("skills_catalog", "profiles_catalog"):
        # Profiles reserve one Choice alternative for keeping the current profile.
        limit = MAX_CHOICE_OPTIONS - (key == "profiles_catalog")
        result[key] = catalog(data.get(key, []), limit=limit)
    essential = data.get("essential_tools", [])
    if (not isinstance(essential, list) or len(essential) > 512
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in essential)):
        raise ValueError("essential_tools must contain bounded tool names")
    result["essential_tools"] = tuple(essential)
    for key, default, low, high in (("routing_budget_seconds", 25.0, 1.0, 25.0),
                                    ("routing_threshold", 0.7, 0.0, 1.0)):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("routing limits must be finite numbers")
        result[key] = max(low, min(float(value), high))
    return result
