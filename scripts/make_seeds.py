"""Build seed/prathosh.json and seed/mom.json (version 1 of each plan), with the
"must fix before import" changes from the app plan (section 10) applied.

    python scripts/make_seeds.py

Prathosh: from seed/raw/prathosh_calendar.json (exported from "Race to 21.1.html").
Mom: her calendar page was not on this laptop, so her plan is rebuilt from the facts in the
app plan (run-walk every other day, 7.5 km first week, long runs 25 Oct / 29 Oct / 15 Nov,
Japan 31 Oct-9 Nov, first week back one step lower). If you export her real calendar to
seed/raw/mom_calendar.json, this script uses it instead.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"

LOC = {"Treadmill": "treadmill", "Outdoor": "outdoor", "Gym": "gym", "—": "none"}


def where(text: str) -> str:
    for k, v in LOC.items():
        if text.startswith(k):
            return v
    return "outdoor"


def kind(x: dict, mom: bool) -> str:
    k, t = x["k"], x["t"].lower()
    if k == "fast":
        if mom:
            return "race_pace"
        if "interval" in t:
            return "intervals"
        if "tempo" in t:
            return "tempo"
        if "time trial" in t:
            return "time_trial"
        return "race_pace"
    return {"easy": "easy", "long": "long", "rest": "rest", "strength": "strength",
            "race": "race", "walk": "walk"}[k]


SPEED = re.compile(r"\s*\(?~?\d+(?:\.\d+)?(?:–\d+(?:\.\d+)?)?\s*km/h\)?")
FUEL_STEP = "Rehearse your race fuel: dates, a banana or raisins, from about 40 minutes in."


def clean_steps(steps: list[str]) -> list[str]:
    out = []
    for s in steps:
        s = re.sub(r"^1% incline,\s*", "", s)
        s = re.sub(r"\s+at\s+~?\d+(?:\.\d+)?(?:–\d+(?:\.\d+)?)?\s*km/h", " at the pace below", s)
        s = SPEED.sub("", s).strip(" ,.") + "."
        s = s.replace("..", ".")
        if re.search(r"\bgels?\b", s, re.I):
            s = re.sub(r"Kit, gels, bib pinned", "Kit, dates or raisins, bib pinned", s)
            if re.search(r"\bgels?\b", s, re.I):
                s = FUEL_STEP
        if s.strip(".") and s not in out:
            out.append(s)
    return out


# Section 10: Prathosh's trimmed weeks (R-05) -> {day: (km, new steps or None)}
PRATHOSH_TRIM = {
    "2026-10-13": (3, ["Easy 3 km.", "Talk test: full sentences."]),
    "2026-10-16": (3, ["3 km easy.", "Then 4 × 20 s strides: fast but relaxed, not a sprint.", "Walk 40 s after each."]),
    "2026-10-18": (5.5, ["Start before 7:30 am.", "Walk 1 min every 2 km.", "Should feel too slow for the first 3 km."]),
    "2026-10-20": (4, ["Easy 4 km."]),
    "2026-10-21": (5.5, ["Warm up 1.5 km easy.", "1.25 km at tempo pace, walk 2 min, repeat.", "Cool down 1.5 km easy."]),
    "2026-10-25": (7.5, ["Start before 7:30 am.", "Walk 1 min every 2 km.", "Drink when thirsty."]),
    "2026-10-28": (6.6, ["Warm up 1.5 km easy.", "3 × 800 m at interval pace.", "400 m walk or very slow jog between.", "Cool down 1.5 km."]),
    "2026-10-30": (4, ["4 km easy, then 4 × 20 s strides."]),
}
FUEL_DAYS = {"2026-11-01", "2026-11-15", "2026-11-22"}


def prathosh() -> dict:
    raw = json.loads((SEED / "raw" / "prathosh_calendar.json").read_text())
    days = []
    for x in raw:
        st = kind(x, mom=False)
        km = float(x["km"] or 0)
        steps = clean_steps(x["steps"])
        if x["d"] in PRATHOSH_TRIM:
            km, steps = PRATHOSH_TRIM[x["d"]]
        if x["d"] in FUEL_DAYS and FUEL_STEP not in steps:
            steps = [s for s in steps if "eat something small" not in s.lower()] + [FUEL_STEP]
        rules = {"rest": ["R-02"], "strength": ["R-22"], "race": ["R-12"], "long": ["R-03"],
                 "time_trial": ["R-25"]}.get(st, ["R-02"] if st in ("tempo", "intervals") else ["R-03"])
        if x["d"] in FUEL_DAYS:
            rules = rules + ["R-19", "R-21"]
        if x["d"] in PRATHOSH_TRIM:
            rules = rules + ["R-05"]
        title = x["t"]
        if x["d"] == "2026-10-28":
            title = "Intervals 3 × 800 m"
        if x["d"] == "2026-10-21":
            title = "Tempo 2 × 1.25 km"
        days.append({"day": x["d"], "session_type": st, "distance_km": km,
                     "location": "outdoor" if st == "race" else where(x["where"]),
                     "run_walk": "continuous" if st not in ("rest", "strength") else None,
                     "title": title, "breaks": None if x["brk"] == "—" else x["brk"],
                     "steps": steps, "rule_ids": rules, "fuel_rehearsal": x["d"] in FUEL_DAYS})
    return {
        "runner": {"name": "Prathosh", "sex": "male", "birth_year": 2004, "height_cm": 171.5,
                   "weight_kg": 62.5, "level": "beginner", "run_walk": 0, "prior_injury": 0,
                   "diet": "no_added_sugar", "diet_confirmed": 1, "has_treadmill": 1,
                   "baseline_week_km": 5, "baseline_longest_km": 5, "goal_time_s": 2 * 3600 + 45 * 60,
                   "race_day": "2026-12-05", "first_week": "2026-10-05", "guide": None,
                   "created_at": "2026-10-09T00:00:00+00:00"},
        "time_trials": [{"day": "2026-10-08", "distance_km": 5, "time_s": 32 * 60, "setting": "treadmill_flat"}],
        "logs": [{"day": "2026-10-08", "status": "done", "distance_km": 5, "minutes": 32, "effort": 9,
                  "location": "treadmill", "pain": "none", "note": "5 km time trial (baseline)"}],
        "availability": [{"start_day": "2026-10-31", "end_day": "2026-11-09", "kind": "travel",
                          "can_run": 1, "climate": "cool", "note": "Japan"}],
        "days": days,
    }


def rw_steps(ratio: str, km: float, extra=None) -> list[str]:
    jog, walk = {"1:1": ("1 minute", "1 minute"), "90/60": ("90 seconds", "60 seconds"),
                 "2:1": ("2 minutes", "1 minute"), "3:1": ("3 minutes", "1 minute"),
                 "4:1": ("4 minutes", "1 minute")}[ratio]
    s = ["Walk 5 minutes to warm up.", f"Jog {jog}, walk {walk}, until you reach {km:g} km.",
         "Go before 7:30 am and drink when thirsty."]
    return s + (extra or [])


def mom() -> dict:
    # (day, type, km, ratio, title, rules, extra steps)
    snack = ["Rehearse your in-run snack from about 40 minutes in."]
    plan = [
        ("2026-10-06", "easy", 2.5, "1:1", "Run-walk 2.5 km", ["R-06"]),
        ("2026-10-08", "easy", 2.5, "1:1", "Run-walk 2.5 km", ["R-06"]),
        ("2026-10-10", "easy", 2.5, "1:1", "Run-walk 2.5 km", ["R-06"]),
        ("2026-10-13", "easy", 3, "1:1", "Run-walk 3 km", ["R-06"]),
        ("2026-10-15", "easy", 3, "1:1", "Run-walk 3 km", ["R-06"]),
        ("2026-10-18", "long", 4.5, "90/60", "Run-walk 4.5 km", ["R-06", "R-03"]),
        ("2026-10-20", "easy", 2.5, "90/60", "Run-walk 2.5 km", ["R-06"]),
        ("2026-10-22", "race_pace", 2.5, "90/60", "Run-walk 2.5 km · race jog", ["R-02"]),
        ("2026-10-25", "long", 8.5, "2:1", "Long run-walk 8.5 km", ["R-03", "R-05"]),  # 9 -> 8.5 (R-05)
        ("2026-10-27", "easy", 5, "2:1", "Run-walk 5 km", ["R-06"]),
        ("2026-10-29", "long", 11, "2:1", "Long run-walk 11 km · before Japan", ["R-13", "R-19"], snack),
        ("2026-11-10", "easy", 4, "90/60", "First run back · 4 km", ["R-33"]),
        ("2026-11-12", "easy", 5, "90/60", "Run-walk 5 km", ["R-33"]),
        ("2026-11-15", "long", 12, "90/60", "Long run-walk 12 km", ["R-33", "R-19"], snack),
        ("2026-11-17", "easy", 5, "2:1", "Run-walk 5 km", ["R-06"]),
        ("2026-11-19", "race_pace", 5, "2:1", "Run-walk 5 km · race jog", ["R-02"]),
        ("2026-11-22", "long", 15, "3:1", "Race rehearsal 15 km", ["R-19", "R-03"],
         ["Start at 4:30–5 am, like race day.", "Rehearse your race snack."]),
        ("2026-11-24", "easy", 5, "3:1", "Run-walk 5 km", ["R-06"]),
        ("2026-11-26", "race_pace", 4, "3:1", "Run-walk 4 km · race jog", ["R-02"]),
        ("2026-11-29", "long", 8, "3:1", "Taper run-walk 8 km", ["R-05"]),
        ("2026-12-01", "race_pace", 3, "3:1", "Run-walk 3 km · race jog", ["R-12"]),
        ("2026-12-03", "easy", 2, "3:1", "Shakeout 2 km", ["R-12"]),
    ]
    walks = {"2026-11-02": "Japan: a 2–3 hour walking day", "2026-11-06": "Japan: a 2–3 hour walking day"}
    strength = {"2026-10-14", "2026-10-21", "2026-10-28", "2026-11-18", "2026-11-25"}
    by = {p[0]: p for p in plan}
    days = []
    from datetime import date, timedelta
    d = date(2026, 10, 5)
    while d <= date(2026, 12, 5):
        k = d.isoformat()
        if k in by:
            p = by[k]
            extra = p[6] if len(p) > 6 else None
            days.append({"day": k, "session_type": p[1], "distance_km": p[2], "location": "outdoor",
                         "run_walk": p[3], "title": p[4], "breaks": None,
                         "steps": rw_steps(p[3], p[2], extra), "rule_ids": p[5],
                         "fuel_rehearsal": "R-19" in p[5]})
        elif k == "2026-12-05":
            days.append({"day": k, "session_type": "race", "distance_km": 21.1, "location": "outdoor",
                         "run_walk": "3:1", "title": "adidas Half Marathon", "breaks": None,
                         "steps": ["Flag-off 4:30 am.", "Jog 3 minutes, walk 1 minute, from the start.",
                                   "Walk through every water station and drink when thirsty."],
                         "rule_ids": ["R-12"], "fuel_rehearsal": False})
        elif k in walks:
            days.append({"day": k, "session_type": "walk", "distance_km": 0, "location": "outdoor",
                         "run_walk": None, "title": "Walking day", "breaks": None,
                         "steps": [walks[k], "Comfortable shoes, rest when you like."],
                         "rule_ids": ["R-33"], "fuel_rehearsal": False})
        elif k in strength:
            days.append({"day": k, "session_type": "strength", "distance_km": 0, "location": "none",
                         "run_walk": None, "title": "Strength · 20 minutes", "breaks": None,
                         "steps": ["Sit-to-stand × 10, wall push-ups × 10, calf raises × 15.",
                                   "2 rounds. Stop before anything hurts."],
                         "rule_ids": ["R-22"], "fuel_rehearsal": False})
        else:
            days.append({"day": k, "session_type": "rest", "distance_km": 0, "location": "none",
                         "run_walk": None, "title": "Rest", "breaks": None,
                         "steps": ["Rest day. A gentle walk is fine."], "rule_ids": ["R-06"],
                         "fuel_rehearsal": False})
        d += timedelta(days=1)
    return {
        "runner": {"name": "Mom", "sex": "female", "birth_year": 1980, "height_cm": 159,
                   "weight_kg": 56.5, "level": "starter", "run_walk": 1, "prior_injury": 0,
                   "diet": "none", "diet_confirmed": 0, "has_treadmill": 0,
                   "baseline_week_km": 0, "baseline_longest_km": 2.0, "goal_time_s": 3 * 3600 + 15 * 60,
                   "race_day": "2026-12-05", "first_week": "2026-10-05",
                   "guide": {"jog": "9:00–10:00", "race_jog": [8.75, 9.25], "walk_brisk": "10:30–12:00",
                             "walk": 11.0, "race_ratio": "3:1"},
                   "created_at": "2026-10-09T00:00:00+00:00"},
        "time_trials": [], "logs": [],
        "availability": [{"start_day": "2026-10-31", "end_day": "2026-11-09", "kind": "travel",
                          "can_run": 0, "climate": "cool", "note": "Japan, no running"}],
        "days": days, "reconstructed": True,
    }


if __name__ == "__main__":
    (SEED / "prathosh.json").write_text(json.dumps(prathosh(), indent=1, ensure_ascii=False))
    (SEED / "mom.json").write_text(json.dumps(mom(), indent=1, ensure_ascii=False))
    print("Wrote seed/prathosh.json and seed/mom.json")
