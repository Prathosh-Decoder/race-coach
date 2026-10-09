"""Decides when prank mode stays silent: pain lock, sick days, hard effort, race week."""
from .. import engine


def reasons(conn, rid: int, day) -> dict:
    r = engine.runner(conn, rid)
    t = day.isoformat()
    lg = engine.logs(conn, rid)
    return {
        "pain": bool(engine.open_pain(conn, rid)),
        "sick": bool(engine.sick_now(conn, rid, t)),
        "screening": engine.screening_state(conn, rid) != "ok",
        "hard_effort": bool(lg and lg[-1]["effort"] and lg[-1]["effort"] >= 8),
        "race_week": engine.race_week(r, day),
        "race_day": t == r["race_day"],
    }


def all_silent(q: dict) -> bool:
    return q["pain"] or q["sick"] or q["screening"]
