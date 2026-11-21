from __future__ import annotations

import json
from dataclasses import dataclass

from .config import CONTROL_ZONES, Config


@dataclass(frozen=True)
class Zone:
    id: str
    label: str
    question: str
    message: str
    urgent: bool = False
    climate: bool = False  # question adapts to room temperature
    detect: str | None = None  # object the scene camera should find at this spot


def load_zones(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    zones: dict = {}
    for item in raw["zones"]:
        zone = Zone(**item)
        if zone.id in CONTROL_ZONES:
            raise ValueError(f"zone id {zone.id!r} is reserved")
        if zone.id in zones:
            raise ValueError(f"duplicate zone id {zone.id!r}")
        zones[zone.id] = zone
    if not zones:
        raise ValueError("zones file has no zones")
    return zones


def load_needs(path: str) -> dict:
    """Question and message for each object the scene camera can recognise."""
    with open(path, encoding="utf-8") as f:
        needs = json.load(f).get("needs", {})
    for word, need in needs.items():
        if not isinstance(need.get("question"), str) or not isinstance(need.get("message"), str):
            raise ValueError(f"need {word!r} must have a question and a message")
    return needs


def _climate(temp_c: float | None, cfg: Config):
    if temp_c is None:
        return None
    if temp_c > cfg.warm_above_c:
        return "Are you too warm?", f"feels too warm (room is {temp_c:.0f}°C)"
    if temp_c < cfg.cold_below_c:
        return "Are you cold?", f"feels cold (room is {temp_c:.0f}°C)"
    return None


def resolve(zone: Zone, temp_c: float | None, cfg: Config):
    """Question and caregiver message for a zone, given the room temperature."""
    if zone.climate:
        adapted = _climate(temp_c, cfg)
        if adapted:
            return adapted
    return zone.question, zone.message


def climate_offer(temp_c: float | None, cfg: Config):
    """Unprompted question when the room is outside the comfort range."""
    adapted = _climate(temp_c, cfg)
    if not adapted:
        return None
    question, message = adapted
    return f"The room is {temp_c:.0f}°C. {question}", message
