"""Plan engine: today's session, paces, prediction, locks, streaks and triggers.
All numbers shown to the runner are calculated here, never by the model."""
import json
import math
from datetime import date, timedelta

from . import clock
from .db import row, rows

RACE_KM = 21.0975
RUN_TYPES = {"easy", "long", "tempo", "intervals", "race_pace", "time_trial", "race"}
HARD_TYPES = {"tempo", "intervals", "race_pace", "time_trial"}
RW_STEPS = ["1:1", "90/60", "2:1", "3:1", "4:1"]
RW_MINUTES = {"1:1": (1, 1), "90/60": (1.5, 1), "2:1": (2, 1), "3:1": (3, 1), "4:1": (4, 1)}

ZONES = {  # multiples of 5 km race pace (convention, grade D)
    "easy": (1.25, 1.37),
    "long": (1.29, 1.37),
    "tempo": (1.09, 1.13),
    "intervals": (0.96, 1.00),
}

TARGET_EFFORT = {"walk": 3, "easy": 4, "long": 4, "tempo": 7, "intervals": 8,
                 "race_pace": 6, "time_trial": 9, "race": 8, "strength": 5, "rest": 1}

EFFORT_TEXT = {
    "walk": ("Brisk walk", "2–3 of 10", "you can sing"),
    "easy": ("Easy", "3–4 of 10", "you can talk in sentences"),
    "long": ("Long and easy", "3–4 of 10", "you can talk in sentences"),
    "tempo": ("Comfortably hard", "6–7 of 10", "you can say a few words"),
    "intervals": ("Hard repeats", "8 of 10", "only a word or two"),
    "race_pace": ("Race effort", "6 of 10", "steady, short sentences"),
    "time_trial": ("Best even effort", "9 of 10", "no talking"),
    "race": ("Race day", "even effort", "start slower than you want"),
    "strength": ("Strength", "moderate", "controlled, no straining"),
    "rest": ("Rest", "—", "feet up"),
}

TYPE_LABEL = {"rest": "Rest", "walk": "Walk", "easy": "Easy run", "long": "Long run",
              "tempo": "Tempo", "intervals": "Intervals", "race_pace": "Race pace",
              "strength": "Strength", "time_trial": "5 km time trial", "race": "Race"}

HEAT_CARD = ("Heat: if you feel confused, unsteady or sick, stop, find shade and cool down. "
             "Go before 7:30 am or after 7 pm.")
WATER_LINE = "Water: drink when thirsty."

FUEL = {
    "none": ["Banana", "Dates", "Raisins"],
    "no_added_sugar": ["Dates", "Banana", "Raisins"],
    "no_sugar": ["Salted boiled potato", "Plain rice ball", "Salted crackers"],
    "low_carb": ["Salted nuts", "Cheese cubes", "Sugar-free electrolyte tablet in water"],
}
EXCLUDED_FOODS = {
    "none": [],
    "no_added_sugar": ["gel", "sports drink", "honey", "candy", "sweets", "jelly bean",
                       "cola", "syrup", "chocolate", "glucose", "energy chew", "gummy"],
    "no_sugar": ["gel", "sports drink", "honey", "candy", "sweets", "jelly bean", "cola",
                 "syrup", "chocolate", "glucose", "energy chew", "gummy", "banana", "date",
                 "raisin", "fruit", "juice"],
    "low_carb": ["gel", "sports drink", "honey", "candy", "sweets", "jelly bean", "cola",
                 "syrup", "chocolate", "glucose", "energy chew", "gummy", "banana", "date",
                 "raisin", "bread", "rice", "potato", "juice"],
}


# ---------- formatting ----------

def fmt_pace(min_per_km: float) -> str:
    total = round(min_per_km * 60)
    return f"{total // 60}:{total % 60:02d}"


def fmt_speed(min_per_km: float) -> str:
    return f"{60 / min_per_km:.1f}"


def fmt_hms(seconds: float, minutes_only: bool = False) -> str:
    if minutes_only:
        m = round(seconds / 60)
        return f"{m // 60}:{m % 60:02d}"
    s = round(seconds)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def parse_pace(text: str) -> float:
    m, s = text.split(":")
    return int(m) + int(s) / 60


# ---------- data access ----------

def runner(conn, rid: int) -> dict:
    r = row(conn, "SELECT * FROM runner WHERE id=?", rid)
    if r and r.get("guide_json"):
        r["guide"] = json.loads(r["guide_json"])
    elif r:
        r["guide"] = {}
    return r


def plan(conn, rid: int) -> dict[str, dict]:
    return {r["day"]: r for r in rows(conn, "SELECT * FROM plan_day WHERE runner_id=? ORDER BY day", rid)}


def logs(conn, rid: int) -> list[dict]:
    return rows(conn, "SELECT * FROM session_log WHERE runner_id=? ORDER BY day, id", rid)


def logs_by_day(conn, rid: int) -> dict[str, dict]:
    out = {}
    for lg in logs(conn, rid):
        out[lg["day"]] = lg  # latest log for a day wins
    return out


def availability(conn, rid: int) -> list[dict]:
    return rows(conn, "SELECT * FROM availability WHERE runner_id=? ORDER BY start_day", rid)


def covering(avail: list[dict], day: str, kind: str | None = None) -> dict | None:
    for a in avail:
        if a["start_day"] <= day <= a["end_day"] and (kind is None or a["kind"] == kind):
            return a
    return None


def is_run(plan_row: dict | None) -> bool:
    return bool(plan_row) and plan_row["session_type"] in RUN_TYPES and (plan_row["distance_km"] or 0) > 0


def run_km_by_day(plan_rows: dict[str, dict], log_rows: dict[str, dict], today: date) -> dict[str, float]:
    """Running km per day: logs for past days (and today once logged), the plan otherwise.
    Walks and strength are excluded; run-walk counts at full distance."""
    out: dict[str, float] = {}
    t = today.isoformat()
    for d, p in plan_rows.items():
        if is_run(p):
            out[d] = float(p["distance_km"])
    for d, lg in log_rows.items():
        p = plan_rows.get(d)
        if p and p["session_type"] in ("walk", "strength"):
            continue
        if d > t:
            continue
        if lg["status"] == "missed":
            out.pop(d, None)
        else:
            km = lg["distance_km"]
            if km is None:
                km = p["distance_km"] if is_run(p) else 0
            if km:
                out[d] = float(km)
            else:
                out.pop(d, None)
    return out


def longest_before(runs: dict[str, float], day: str, baseline: float) -> float:
    d = clock.parse(day)
    lo = (d - timedelta(days=30)).isoformat()
    vals = [km for k, km in runs.items() if lo <= k < day]
    return max(vals) if vals else baseline


def week_total(runs: dict[str, float], monday: date) -> float:
    end = (monday + timedelta(days=6)).isoformat()
    return round(sum(km for d, km in runs.items() if monday.isoformat() <= d <= end), 2)


def week_sessions(runs: dict[str, float], monday: date) -> int:
    end = (monday + timedelta(days=6)).isoformat()
    return sum(1 for d in runs if monday.isoformat() <= d <= end)


# ---------- paces and prediction ----------

def latest_trial(conn, rid: int) -> dict | None:
    return row(conn, "SELECT * FROM time_trial WHERE runner_id=? ORDER BY day DESC, id DESC LIMIT 1", rid)


def five_k_pace(trial: dict) -> float:
    """5 km-equivalent race pace in min/km (Riegel if the trial was not 5 km)."""
    t5 = trial["time_s"] * (5 / trial["distance_km"]) ** 1.06
    return t5 / 60 / 5


def heat_range(setting: str) -> tuple[float, float]:
    return (0.0, 0.05) if setting == "outdoor_heat" else (0.05, 0.12)


def riegel_half(trial: dict) -> float:
    return trial["time_s"] * (RACE_KM / trial["distance_km"]) ** 1.06


def prediction(conn, r: dict) -> dict | None:
    if r["run_walk"]:
        return runwalk_prediction(conn, r)
    trial = latest_trial(conn, r["id"])
    if not trial:
        return None
    half = riegel_half(trial)
    h_lo, h_hi = heat_range(trial["setting"])
    lo = half * (1 + h_lo) + 60
    hi = half * (1 + h_hi) + 180
    return {"kind": "riegel", "formula_s": half, "formula": fmt_hms(half),
            "low_s": lo, "high_s": hi,
            "low": fmt_hms(lo, True), "high": fmt_hms(hi, True),
            "race_pace": (half * (1 + h_lo) / 60 / 21.1, half * (1 + h_hi) / 60 / 21.1),
            "trial": trial, "rules": ["R-25", "R-26"]}


def runwalk_prediction(conn, r: dict) -> dict | None:
    g = r.get("guide") or {}
    ratio = g.get("race_ratio", "3:1")
    # Real pace from a 10 km+ run-walk at race ratio wins over the guide paces.
    real = rows(conn, """SELECT l.* FROM session_log l JOIN plan_day p
                         ON p.runner_id=l.runner_id AND p.day=l.day
                         WHERE l.runner_id=? AND l.status='done' AND l.distance_km>=10
                         AND p.run_walk=? ORDER BY l.day DESC LIMIT 1""", r["id"], ratio)
    if real:
        p = real[0]["minutes"] / real[0]["distance_km"]
        lo, hi = RACE_KM * p + 2, RACE_KM * p + 4
        return {"kind": "runwalk_real", "low_s": lo * 60, "high_s": hi * 60,
                "low": fmt_hms(lo * 60, True), "high": fmt_hms(hi * 60, True),
                "race_pace": (p, p), "rules": ["R-25"]}
    if "race_jog" not in g:
        return None
    rr, ww = RW_MINUTES[ratio]
    walk = g.get("walk", 11.0)

    def avg(jog):
        return (rr + ww) / (rr / jog + ww / walk)

    p_lo, p_hi = avg(g["race_jog"][0]), avg(g["race_jog"][1])
    lo, hi = RACE_KM * p_lo + 2, RACE_KM * p_hi + 4
    return {"kind": "runwalk", "ratio": ratio, "low_s": lo * 60, "high_s": hi * 60,
            "low": fmt_hms(lo * 60, True), "high": fmt_hms(hi * 60, True),
            "race_pace": (p_lo, p_hi), "rules": ["R-25"]}


def paces(conn, r: dict) -> dict:
    """Zone paces (min/km) and treadmill speeds at 1% incline (R-27)."""
    if r["run_walk"]:
        g = r.get("guide") or {}
        return {"run_walk": True, "jog": g.get("jog", "9:00–10:00"),
                "race_jog": g.get("race_jog"), "walk": g.get("walk_brisk", "10:30–12:00")}
    trial = latest_trial(conn, r["id"])
    if not trial:
        return {"run_walk": False, "zones": {}}
    p5 = five_k_pace(trial)
    zones = {}
    for z, (a, b) in ZONES.items():
        fast, slow = p5 * a, p5 * b
        zones[z] = {"pace": f"{fmt_pace(fast)}–{fmt_pace(slow)}",
                    "speed": f"{fmt_speed(slow)}–{fmt_speed(fast)}", "fast": fast, "slow": slow}
    pred = prediction(conn, r)
    if pred:
        fast, slow = pred["race_pace"]
        zones["race"] = {"pace": f"{fmt_pace(fast)}–{fmt_pace(slow)}",
                         "speed": f"{fmt_speed(slow)}–{fmt_speed(fast)}", "fast": fast, "slow": slow}
    zones["long"]["note"] = "walk breaks OK"
    return {"run_walk": False, "zones": zones, "five_k_pace": p5}


ZONE_FOR = {"easy": "easy", "long": "long", "tempo": "tempo", "intervals": "intervals",
            "race_pace": "race", "time_trial": "intervals", "race": "race"}


def pace_band(conn, r: dict) -> list[dict]:
    """Goal line pace band for 5, 10 and 15 km. Goal never changes training paces."""
    if not r["goal_time_s"]:
        return []
    per_km = r["goal_time_s"] / RACE_KM
    return [{"km": k, "time": fmt_hms(per_km * k)} for k in (5, 10, 15)] + \
           [{"km": 21.1, "time": fmt_hms(r["goal_time_s"])}]


# ---------- locks and flags ----------

def screening_state(conn, rid: int) -> str:
    """'unanswered', 'locked' (needs doctor's clearance) or 'ok' (R-32)."""
    s = row(conn, "SELECT * FROM screening WHERE runner_id=? ORDER BY id DESC LIMIT 1", rid)
    if not s:
        return "unanswered"
    if (s["known_disease"] or s["symptoms"]) and not s["doctor_cleared_on"]:
        return "locked"
    return "ok"


def open_pain(conn, rid: int) -> dict | None:
    return row(conn, """SELECT * FROM session_log WHERE runner_id=? AND pain='sharp'
                        AND pain_resolved_at IS NULL ORDER BY day DESC LIMIT 1""", rid)


def sick_now(conn, rid: int, day: str) -> dict | None:
    return covering(availability(conn, rid), day, "sick")


def hard_hold(conn, rid: int, today: date) -> dict | None:
    """A run 3+ points harder than planned holds distance increases for 7 days (R-04)."""
    p = plan(conn, rid)
    lo = (today - timedelta(days=7)).isoformat()
    for lg in reversed(logs(conn, rid)):
        if lg["day"] < lo or lg["status"] == "missed" or not lg["effort"]:
            continue
        target = TARGET_EFFORT.get(p.get(lg["day"], {}).get("session_type", "easy"), 4)
        if lg["effort"] >= target + 3:
            until = clock.parse(lg["day"]) + timedelta(days=7)
            return {"day": lg["day"], "effort": lg["effort"], "target": target,
                    "until": until.isoformat()}
    return None


def poor_sleep(conn, rid: int) -> bool:
    slept = [lg["sleep"] for lg in logs(conn, rid) if lg["sleep"]]
    return len(slept) >= 2 and slept[-1] == "poor" and slept[-2] == "poor"


def runwalk_progress(conn, r: dict, today: date) -> dict | None:
    """Two run-walk sessions at effort 5 or below, pain-free, unlock the next step (R-06)."""
    if not r["run_walk"]:
        return None
    p = plan(conn, r["id"])
    recent = [lg for lg in logs(conn, r["id"]) if lg["day"] <= today.isoformat()
              and lg["status"] == "done" and p.get(lg["day"], {}).get("run_walk") in RW_STEPS]
    if not recent:
        return None
    current = p[recent[-1]["day"]]["run_walk"]
    same = [lg for lg in recent if p[lg["day"]]["run_walk"] == current][-2:]
    ok = len(same) == 2 and all(lg["effort"] <= 5 and lg["pain"] == "none" for lg in same)
    i = RW_STEPS.index(current)
    nxt = RW_STEPS[i + 1] if i + 1 < len(RW_STEPS) else None
    return {"current": current, "next": nxt, "unlocked": bool(ok and nxt)}


def unanswered_days(conn, r: dict, today: date) -> list[str]:
    """Past run days with no log (since setup, last 7 days), newest first."""
    p = plan(conn, r["id"])
    lg = logs_by_day(conn, r["id"])
    start = max(clock.to_sgt(r["created_at"]).date(), today - timedelta(days=7))
    out = []
    for d in clock.daterange(start, today - timedelta(days=1)):
        k = d.isoformat()
        if is_run(p.get(k)) and k not in lg:
            out.append(k)
    return sorted(out, reverse=True)


def streak(conn, r: dict, today: date) -> int:
    """Consecutive good days: a run done, or a rest day actually rested.
    Extra running never adds to it."""
    p = plan(conn, r["id"])
    lg = logs_by_day(conn, r["id"])
    start = clock.to_sgt(r["created_at"]).date()
    n = 0
    d = today if today.isoformat() in lg else today - timedelta(days=1)
    while d >= start:
        k = d.isoformat()
        pr = p.get(k)
        log = lg.get(k)
        if is_run(pr) or (pr and pr["session_type"] == "walk"):
            if not log or log["status"] == "missed":
                break
        elif log and log["status"] != "missed" and (log["distance_km"] or 0) > 0:
            pass  # ran on a rest day: no credit, no break
        n += 1
        d -= timedelta(days=1)
    return n


def race_week(r: dict, day: date) -> bool:
    race = clock.parse(r["race_day"])
    return clock.week_start(race) <= day <= race


# ---------- session card ----------

def _steps_doc(p: dict) -> dict:
    try:
        doc = json.loads(p.get("steps_json") or "{}")
    except json.JSONDecodeError:
        doc = {}
    return doc if isinstance(doc, dict) else {"steps": doc}


def estimate_minutes(r: dict, pc: dict, p: dict) -> float | None:
    km = p["distance_km"] or 0
    if not km:
        return None
    if pc.get("run_walk"):
        g = r.get("guide") or {}
        jog = parse_pace(g.get("jog", "9:00–10:00").split("–")[1])
        walk = 11.0
        rr, ww = RW_MINUTES.get(p.get("run_walk") or "1:1", (1, 1))
        avg = (rr + ww) / (rr / jog + ww / walk)
        return km * avg
    z = pc.get("zones", {}).get(ZONE_FOR.get(p["session_type"], "easy"))
    if not z:
        return km * 8.5
    if p["session_type"] in ("intervals", "tempo", "time_trial"):
        return km * (pc["zones"]["easy"]["slow"] + z["slow"]) / 2
    return km * z["slow"]


def runwalk_text(ratio: str | None) -> str:
    if not ratio or ratio in ("continuous", "none"):
        return ""
    rr, ww = RW_MINUTES.get(ratio, (1, 1))
    fm = lambda m: f"{int(m)} min" if m == int(m) else f"{int(m * 60)} s"
    return f"{fm(rr)} jog · {fm(ww)} walk"


def session_card(conn, r: dict, day: str, p: dict | None, *, cool_climate: bool = False) -> dict:
    """Everything the session card shows. Paces, speeds, fuel and the heat card come from code."""
    if not p:
        p = {"day": day, "session_type": "rest", "distance_km": 0, "location": "none",
             "run_walk": None, "steps_json": None, "rule_ids": "[]", "elevated_risk": 0,
             "runner_ok_at": None, "source": "manual", "zone": None}
    st = p["session_type"]
    doc = _steps_doc(p)
    pc = paces(conn, r)
    location = p["location"] or "none"
    if location in ("treadmill", "either") and not r["has_treadmill"]:
        location = "outdoor"
    km = p["distance_km"] or 0
    title = doc.get("title") or TYPE_LABEL[st]
    label, score, cue = EFFORT_TEXT[st]

    pace_text, speed_text = None, None
    if pc.get("run_walk") and st in RUN_TYPES:
        pace_text = "Jog: can talk · Walk: brisk"
        guide = f"Guide if your phone records it: jog {pc['jog']} /km, brisk walk {pc['walk']} /km"
    else:
        guide = None
        z = pc.get("zones", {}).get(ZONE_FOR.get(st, ""))
        if z and st in RUN_TYPES:
            pace_text = f"{z['pace']} /km"
            if location in ("treadmill", "either"):
                speed_text = f"{z['speed']} km/h at 1% incline"
    if cool_climate and st in RUN_TYPES:
        pace_text = "By effort (cool climate)"
        speed_text = None

    breaks = runwalk_text(p.get("run_walk")) or doc.get("breaks") or ("—" if st in ("rest",) else "As needed")
    steps = list(doc.get("steps") or [])
    if st in RUN_TYPES | {"walk"}:
        if not any("warm" in s.lower() for s in steps):
            steps.insert(0, "Warm up: walk 5 minutes.")
        if not any("cool" in s.lower() for s in steps):
            steps.append("Cool down: walk 5 minutes, then stretch gently.")
    minutes = estimate_minutes(r, pc, p) if st in RUN_TYPES | {"walk"} else None
    fuel = None
    if minutes and minutes > 75:
        fuel = {"items": FUEL[r["diet"]], "rule": "R-21",
                "text": "Fuel from your list, from about 40 minutes in, a little every 20–30 minutes."}
    rule_ids = json.loads(p.get("rule_ids") or "[]")
    return {
        "day": day, "type": st, "type_label": TYPE_LABEL[st], "title": title,
        "distance_km": km, "location": location,
        "where": {"treadmill": "Treadmill", "outdoor": "Outdoors", "either": "Treadmill or outdoors",
                  "gym": "Gym", "none": "—"}[location],
        "pace": pace_text, "speed": speed_text, "guide": guide, "breaks": breaks,
        "run_walk": p.get("run_walk"),
        "effort": {"label": label, "score": score, "cue": cue},
        "steps": steps, "minutes": round(minutes) if minutes else None,
        "water": WATER_LINE if st in RUN_TYPES | {"walk"} else None,
        "fuel": fuel,
        "heat": HEAT_CARD if location == "outdoor" and st in RUN_TYPES | {"walk"} else None,
        "reason": doc.get("reason"), "rule_ids": rule_ids,
        "elevated_risk": bool(p.get("elevated_risk")), "runner_ok_at": p.get("runner_ok_at"),
        "source": p.get("source"),
    }


def blocked_card(day: str, kind: str) -> dict:
    if kind == "pain":
        steps = ["No running today.",
                 "Walking is fine only if it is completely pain-free.",
                 "If it is not better in 3 days or gets worse, see a doctor or physiotherapist."]
        title, rules = "No running · pain", ["R-07"]
    else:
        steps = ["Rest until you feel better.", "Tell the app when you are better."]
        title, rules = "Rest · sick", ["R-33"]
    return {"day": day, "type": "rest", "type_label": "Rest", "title": title, "distance_km": 0,
            "location": "none", "where": "—", "pace": None, "speed": None, "guide": None,
            "breaks": "—", "effort": {"label": "Rest", "score": "—", "cue": "feet up"},
            "steps": steps, "minutes": None, "water": None, "fuel": None, "heat": None,
            "reason": None, "rule_ids": rules, "elevated_risk": False, "blocked": kind}


def today_view(conn, rid: int, today: date | None = None) -> dict:
    today = today or clock.today()
    r = runner(conn, rid)
    t = today.isoformat()
    gate = screening_state(conn, rid)
    p = plan(conn, rid)
    pain = open_pain(conn, rid)
    sick = sick_now(conn, rid, t)
    avail = availability(conn, rid)
    trip = covering(avail, t, "travel")
    cool = bool(trip and trip["climate"] == "cool" and trip["can_run"])
    if pain and is_run(p.get(t)):
        card = blocked_card(t, "pain")
    elif sick:
        card = blocked_card(t, "sick")
    else:
        card = session_card(conn, r, t, p.get(t), cool_climate=cool)
    week_label = None
    first = clock.parse(r["first_week"])
    wk = (clock.week_start(today) - first).days // 7
    if card["type"] != "rest" or True:
        week_label = f"week {wk}" if wk >= 1 else "starter week"
    flags = []
    if poor_sleep(conn, rid):
        flags.append({"kind": "sleep", "text": "Two poor nights of sleep. Keep the next hard session easy (R-31)."})
    hold = hard_hold(conn, rid, today)
    if hold and today.isoformat() <= hold["until"]:
        flags.append({"kind": "hold", "text": f"Your run on {hold['day']} felt harder than planned. "
                      "No distance goes up for 7 days unless you say OK (R-04)."})
    prog = runwalk_progress(conn, r, today)
    if prog and prog["unlocked"]:
        flags.append({"kind": "progress", "text": f"Two comfortable sessions at {prog['current']}. "
                      f"Your next step, {prog['next']}, is unlocked (R-06)."})
    if pain:
        flags.append({"kind": "pain", "text": "Sharp pain logged. All runs are paused until you say it is gone (R-07)."})
    if trip and not trip["can_run"]:
        flags.append({"kind": "travel", "text": f"Travelling ({trip.get('note') or 'trip'}): no running. Walking is great."})
    if cool:
        flags.append({"kind": "travel", "text": "Cool climate: run by effort, not by pace (R-14)."})
    if not r["diet_confirmed"]:
        flags.append({"kind": "diet", "text": "Your diet is not set yet. Add it in Profile so the fuel list fits you."})
    race = clock.parse(r["race_day"])
    last_log = logs(conn, rid)
    return {
        "runner": {"id": r["id"], "name": r["name"]},
        "today": t, "days_to_race": (race - today).days, "race_day": r["race_day"],
        "gate": gate, "card": card if gate == "ok" else None, "week_label": week_label,
        "flags": flags, "streak": streak(conn, r, today),
        "logged_today": t in logs_by_day(conn, rid),
        "unanswered": unanswered_days(conn, r, today) if gate == "ok" else [],
        "pain": bool(pain), "sick": bool(sick),
        "race_week": race_week(r, today), "is_race_day": today == race,
        "last_effort": last_log[-1]["effort"] if last_log else None,
    }


def weekly_km(conn, rid: int, today: date, weeks: int = 4) -> list[dict]:
    runs = run_km_by_day(plan(conn, rid), logs_by_day(conn, rid), today)
    mon = clock.week_start(today)
    out = []
    for i in range(weeks, 0, -1):
        m = mon - timedelta(days=7 * i)
        out.append({"week": m.isoformat(), "km": week_total(runs, m)})
    return out


def longest_30(conn, rid: int, today: date) -> float:
    r = runner(conn, rid)
    runs = run_km_by_day(plan(conn, rid), logs_by_day(conn, rid), today)
    past = {d: km for d, km in runs.items() if d <= today.isoformat()}
    return longest_before(past, (today + timedelta(days=1)).isoformat(), r["baseline_longest_km"])
