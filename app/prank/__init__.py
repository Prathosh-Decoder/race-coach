"""Prank mode (docs/PRANK_MODE.md). Display only: the rest of the app only ever asks it
for an extras object. It never changes the plan, logs, checks, prompts or exports."""
import random

from .. import clock, config, engine
from . import bigday, pick, quiet, state
from .knock import knock  # noqa: F401


def extras_for(conn, rid: int, today=None, card: dict | None = None) -> dict | None:
    """Everything prank mode adds to Today, or None when it is off."""
    today = today or clock.today()
    if config.prank_silenced():
        return None
    s = state.get(conn, rid)
    if not s["enabled"]:
        return None
    r = engine.runner(conn, rid)
    q = quiet.reasons(conn, rid, today)
    flav = pick.flavour_for(s["flavour"], rid, today)
    days = (clock.parse(r["race_day"]) - today).days
    countdown = pick.meta(s["flavour"], "countdown").format(n=days) if days > 1 else None
    if days == 1:
        countdown = "1 sleep to race" if flav == "goose" and s["flavour"] == "goose" else "1 day to go"
    if days <= 0:
        countdown = "Race day" if days == 0 else None
    armed = state.big_day_active(s)
    if armed and countdown:
        countdown += "."
    out = {"countdown": countdown, "flavour": s["flavour"], "nickname": None, "greeting": None,
           "saved": None, "side_quest": None, "loading": None, "spin": None, "big_day": None,
           "sound": None}
    if q["screening"]:
        return out  # health questions: never any prank content
    n = (today - pick.EPOCH).days
    if quiet.all_silent(q):
        return out
    out["nickname"] = (pick.meta(flav, "nickname") if n % 2 == 0 else None) or \
        (pick.lines().get("names") or {}).get(r["name"])
    if q["race_day"]:
        out["greeting"] = pick.line(rid, flav, "race_morning", today)
        return out
    rest_day = not card or card.get("type") in ("rest", "strength")
    if not q["hard_effort"]:
        out["greeting"] = pick.line(rid, flav, "rest" if rest_day else "greeting", today)
    out["saved"] = pick.line(rid, flav, "saved", today)
    out["loading"] = pick.line(rid, flav, "loading", today)
    if rest_day:
        out["side_quest"] = {"label": pick.meta(flav, "side_label"),
                             "text": pick.line(rid, flav, "side_quest", today)}
    elif not q["race_week"] and card and engine.RUN_TYPES & {card.get("type")}:
        rng = random.Random(f"spin:{rid}:{today}")
        out["spin"] = [rng.choice([15 + 0.5 * i for i in range(17)]) for _ in range(3)]
    if armed and card and bigday.allowed(conn, rid, today, q):
        out["big_day"] = bigday.view(conn, r, card, s["big_day_km"])
        out["spin"] = None
    if s["sound"]:
        out["sound"] = pick.meta(flav, "sound")
    return out


def countdown_only(conn, rid: int, today=None) -> str | None:
    e = extras_for(conn, rid, today)
    return e["countdown"] if e else None
