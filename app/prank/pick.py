"""Chooses today's line for each slot. A fixed shuffled order per profile, flavour and
slot; day n shows item n, so a line holds all day and repeats only after the whole list."""
import random
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

EPOCH = date(2026, 1, 1)
DAILY = ["goose", "royal", "commentator", "trailer", "son"]


@lru_cache
def lines() -> dict:
    return yaml.safe_load((Path(__file__).parent / "lines.yaml").read_text())


def flavour_for(flavour: str, rid: int, day: date) -> str:
    """Surprise picks one of the five flavours from the date."""
    if flavour != "surprise":
        return flavour
    return DAILY[random.Random(f"surprise:{rid}:{day.isoformat()}").randrange(len(DAILY))]


def line(rid: int, flavour: str, slot: str, day: date) -> str | None:
    items = lines()[flavour].get(slot) or []
    if not items:
        return None
    order = list(range(len(items)))
    random.Random(f"{rid}:{flavour}:{slot}").shuffle(order)
    n = (day - EPOCH).days
    return items[order[n % len(items)]]


def meta(flavour: str, key: str):
    return lines()[flavour].get(key)
