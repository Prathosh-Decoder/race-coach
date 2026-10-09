"""Import seed plans as version 1. Imports only when no MUST check fails.

    python -m app.seed            # import both seeds into the real database (once)
    python -m app.seed --check    # validate only, into a throwaway database
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import clock, config, db, validator

SEEDS = ["prathosh.json", "mom.json"]


def create_runner(conn, r: dict) -> int:
    cur = conn.execute("""INSERT INTO runner (name, sex, birth_year, height_cm, weight_kg, level, run_walk,
        prior_injury, diet, diet_confirmed, has_treadmill, baseline_week_km, baseline_longest_km,
        goal_time_s, race_day, first_week, guide_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r["name"], r.get("sex"), r.get("birth_year"), r.get("height_cm"), r.get("weight_kg"),
         r["level"], r.get("run_walk", 0), r.get("prior_injury", 0), r.get("diet", "none"),
         r.get("diet_confirmed", 1), r.get("has_treadmill", 1), r["baseline_week_km"],
         r["baseline_longest_km"], r.get("goal_time_s"), r.get("race_day", "2026-12-05"),
         r["first_week"], json.dumps(r["guide"]) if r.get("guide") else None,
         r.get("created_at") or clock.utc_iso()))
    rid = cur.lastrowid
    conn.execute("INSERT INTO prank_state (runner_id, changed_at) VALUES (?,?)", (rid, clock.utc_iso()))
    return rid


def import_seed(conn, data: dict, today: date | None = None) -> dict:
    conn.execute("BEGIN")
    rid = create_runner(conn, data["runner"])
    for t in data.get("time_trials", []):
        conn.execute("INSERT INTO time_trial (runner_id, day, distance_km, time_s, setting) VALUES (?,?,?,?,?)",
                     (rid, t["day"], t["distance_km"], t["time_s"], t["setting"]))
    for lg in data.get("logs", []):
        conn.execute("""INSERT INTO session_log (runner_id, day, status, distance_km, minutes, location,
                        effort, pain, note, logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (rid, lg["day"], lg["status"], lg.get("distance_km"), lg.get("minutes"),
                      lg.get("location"), lg.get("effort"), lg.get("pain", "none"), lg.get("note"),
                      data["runner"].get("created_at") or clock.utc_iso()))
    for a in data.get("availability", []):
        conn.execute("""INSERT INTO availability (runner_id, start_day, end_day, kind, can_run, climate, note)
                        VALUES (?,?,?,?,?,?,?)""",
                     (rid, a["start_day"], a["end_day"], a["kind"], a["can_run"], a.get("climate"), a.get("note")))
    conn.execute("COMMIT")
    res = validator.validate(conn, rid, data["days"], seed=True, today=today or clock.today())
    if not res["ok"]:
        conn.execute("BEGIN")
        for t in ("prank_state", "time_trial", "session_log", "availability"):
            conn.execute(f"DELETE FROM {t} WHERE runner_id=?", (rid,))
        conn.execute("DELETE FROM runner WHERE id=?", (rid,))
        conn.execute("COMMIT")
        return {"ok": False, "name": data["runner"]["name"], "failures": res["failures"]}
    validator.apply(conn, rid, data["days"], source="seed", seed=True, today=today or clock.today())
    return {"ok": True, "name": data["runner"]["name"], "runner_id": rid,
            "elevated": res["elevated"]}


def load(name: str) -> dict:
    return json.loads((config.ROOT / "seed" / name).read_text())


def main(argv):
    check = "--check" in argv
    if check:
        tmp = Path(tempfile.mkdtemp()) / "check.db"
        conn = db.connect(tmp)
    else:
        conn = db.connect()
    db.migrate(conn)
    names = {r["name"] for r in db.rows(conn, "SELECT name FROM runner")}
    bad = False
    for f in SEEDS:
        data = load(f)
        if data["runner"]["name"] in names:
            print(f"{data['runner']['name']}: already imported, skipped")
            continue
        out = import_seed(conn, data)
        if out["ok"]:
            print(f"{out['name']}: imported, all MUST checks pass")
            for d, why in sorted(out["elevated"].items()):
                print(f"   needs runner's OK (R-04) {d}: {why}")
        else:
            bad = True
            print(f"{out['name']}: NOT imported")
            for x in out["failures"]:
                print("   ", x)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
