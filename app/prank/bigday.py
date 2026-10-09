"""Big Day: the fake distance, title, steps and time. Display only; never writes to the plan."""
import random

from .. import engine


def draw_km(rng: random.Random | None = None) -> float:
    rng = rng or random.Random()
    if rng.random() < 0.2:
        return rng.choice([21, 21.5, 22, 22.5, 23])
    return rng.choice([15 + 0.5 * i for i in range(11)])  # 15-20 in 0.5 steps


def allowed(conn, rid: int, day, quiet: dict) -> bool:
    p = engine.plan(conn, rid).get(day.isoformat())
    return engine.is_run(p) and p["session_type"] != "race" and not (
        quiet["pain"] or quiet["sick"] or quiet["race_week"] or quiet["screening"])


def view(conn, r: dict, real_card: dict, km: float) -> dict:
    """Rewrite the card's distance, title, steps and time using the real paces and ratio."""
    fake = {**engine.plan(conn, r["id"]).get(real_card["day"], {})}
    fake.update(distance_km=km, session_type="long")
    pc = engine.paces(conn, r)
    minutes = engine.estimate_minutes(r, pc, fake)
    rw = engine.runwalk_text(real_card.get("run_walk"))
    if rw:
        title = f"Long run-walk {km:g} km"
        main = f"{rw}, until you reach {km:g} km."
    else:
        title = f"Long run {km:g} km"
        main = f"{km:g} km at an easy, steady effort. Walk 1 minute every 3 km."
    steps = ["Walk 5 minutes to warm up.", main.capitalize() if rw else main,
             "Go before 7:30 am and drink when thirsty.",
             "Cool down: walk 5 minutes, then stretch gently."]
    return {"km": km, "title": title, "steps": steps, "minutes": round(minutes) if minutes else None}
