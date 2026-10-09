"""Loads rules/brief_v1.yaml."""
from functools import lru_cache

import yaml

from . import config


@lru_cache
def load() -> list[dict]:
    with open(config.RULES_PATH) as f:
        return yaml.safe_load(f)["rules"]


def ids() -> set[str]:
    return {r["id"] for r in load()}


def by_strength(strength: str) -> list[dict]:
    return [r for r in load() if r["strength"] == strength]


def must_ids() -> set[str]:
    return {r["id"] for r in by_strength("MUST")}


def prompt_text(strengths=("SHOULD", "MAY")) -> str:
    """Rules word for word with IDs, skipping rules whose wording is not yet copied in."""
    lines = []
    for r in load():
        if r["strength"] in strengths and not r.get("text_pending"):
            lines.append(f"{r['id']} ({r['strength']}, grade {r['grade']}): {r['text']}")
    return "\n".join(lines)
