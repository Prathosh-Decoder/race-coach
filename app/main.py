"""FastAPI app: serves the screens and a small JSON API on 127.0.0.1 only.

    python -m app.main      # migrate, back up, import seeds on first run, open the browser
"""
import json
import socket
import tempfile
import threading
import webbrowser
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import clock, coach, config, db, engine, prank, rules, seed, validator
from .prank import state as prank_state

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Race Coach", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def need_runner(conn, rid: int) -> dict:
    r = engine.runner(conn, rid)
    if not r:
        raise HTTPException(404, "No such runner")
    return r


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


# ---------------- runners, setup and the health gate ----------------

@app.get("/api/runners")
def runners(conn=Depends(get_conn)):
    out = []
    for r in db.rows(conn, "SELECT id, name FROM runner ORDER BY id"):
        out.append({**r, "gate": engine.screening_state(conn, r["id"])})
    return out


class Screening(BaseModel):
    active_3x_week: bool
    known_disease: bool
    symptoms: bool
    doctor_cleared_on: str | None = None


@app.post("/api/runners/{rid}/screening")
def save_screening(rid: int, s: Screening, conn=Depends(get_conn)):
    """R-32: a 'yes' to question 2 or 3 locks the plan until a clearance date is entered.
    Question 1 only sets the level."""
    need_runner(conn, rid)
    conn.execute("""INSERT INTO screening (runner_id, answered_on, active_3x_week, known_disease,
                    symptoms, doctor_cleared_on) VALUES (?,?,?,?,?,?)""",
                 (rid, clock.today().isoformat(), int(s.active_3x_week), int(s.known_disease),
                  int(s.symptoms), s.doctor_cleared_on or None))
    return {"gate": engine.screening_state(conn, rid)}


class Clearance(BaseModel):
    doctor_cleared_on: str


@app.post("/api/runners/{rid}/clearance")
def save_clearance(rid: int, c: Clearance, conn=Depends(get_conn)):
    need_runner(conn, rid)
    clock.parse(c.doctor_cleared_on)  # must be a real date
    last = db.row(conn, "SELECT id FROM screening WHERE runner_id=? ORDER BY id DESC LIMIT 1", rid)
    if not last:
        raise HTTPException(400, "Answer the health questions first.")
    conn.execute("UPDATE screening SET doctor_cleared_on=? WHERE id=?", (c.doctor_cleared_on, last["id"]))
    return {"gate": engine.screening_state(conn, rid)}


class ProfileEdit(BaseModel):
    diet: str | None = None
    goal_time: str | None = None      # "h:mm" or "h:mm:ss"
    has_treadmill: bool | None = None


DIETS = {"none": "No restriction", "no_added_sugar": "No added sugar",
         "no_sugar": "No sugar, including fruit", "low_carb": "Low carbohydrate"}


@app.post("/api/runners/{rid}/profile")
def edit_profile(rid: int, p: ProfileEdit, conn=Depends(get_conn)):
    need_runner(conn, rid)
    if p.diet is not None:
        if p.diet not in DIETS:
            raise HTTPException(400, "Unknown diet")
        conn.execute("UPDATE runner SET diet=?, diet_confirmed=1 WHERE id=?", (p.diet, rid))
    if p.goal_time:
        parts = [int(x) for x in p.goal_time.split(":")]
        while len(parts) < 3:
            parts.append(0)
        secs = parts[0] * 3600 + parts[1] * 60 + parts[2]
        if not 3600 <= secs <= 5 * 3600:
            raise HTTPException(400, "Goal time should be between 1:00 and 5:00")
        conn.execute("UPDATE runner SET goal_time_s=? WHERE id=?", (secs, rid))
    if p.has_treadmill is not None:
        conn.execute("UPDATE runner SET has_treadmill=? WHERE id=?", (int(p.has_treadmill), rid))
    return profile(rid, conn)


@app.get("/api/runners/{rid}/profile")
def profile(rid: int, conn=Depends(get_conn)):
    r = need_runner(conn, rid)
    s = db.row(conn, "SELECT * FROM screening WHERE runner_id=? ORDER BY id DESC LIMIT 1", rid)
    trials = db.rows(conn, "SELECT * FROM time_trial WHERE runner_id=? ORDER BY day DESC", rid)
    avail = engine.availability(conn, rid)
    age = clock.today().year - r["birth_year"] if r["birth_year"] else None
    return {
        "id": r["id"], "name": r["name"], "sex": r["sex"], "age": age,
        "height_cm": r["height_cm"], "weight_kg": r["weight_kg"], "level": r["level"],
        "run_walk": bool(r["run_walk"]), "has_treadmill": bool(r["has_treadmill"]),
        "prior_injury": bool(r["prior_injury"]),
        "diet": r["diet"], "diet_label": DIETS[r["diet"]], "diet_confirmed": bool(r["diet_confirmed"]),
        "diets": DIETS, "fuel": engine.FUEL[r["diet"]],
        "goal_time": engine.fmt_hms(r["goal_time_s"], True) if r["goal_time_s"] else None,
        "baseline_week_km": r["baseline_week_km"], "baseline_longest_km": r["baseline_longest_km"],
        "screening": s, "gate": engine.screening_state(conn, rid),
        "time_trials": [{**t, "time": engine.fmt_hms(t["time_s"])} for t in trials],
        "availability": avail,
        "body": body_card(r),
    }


def body_card(r: dict) -> dict | None:
    if not (r["height_cm"] and r["weight_kg"]):
        return None
    bmi = r["weight_kg"] / (r["height_cm"] / 100) ** 2
    return {"height_cm": r["height_cm"], "weight_kg": r["weight_kg"], "bmi": round(bmi, 1)}


# ---------------- Today ----------------

def _maybe_sunday_review(conn, rid: int):
    """Sunday evening review: the model drafts the next 7 days once per week."""
    now = clock.now()
    if now.weekday() != 6 or now.hour < 18 or engine.screening_state(conn, rid) != "ok":
        return
    tag = f"sunday review {now.date().isoformat()}"
    if db.row(conn, "SELECT id FROM proposal WHERE runner_id=? AND trigger=?", rid, tag):
        return
    coach.start_draft(None, rid, tag)


@app.get("/api/today")
def today(rid: int, conn=Depends(get_conn)):
    need_runner(conn, rid)
    view = engine.today_view(conn, rid)
    view["extras"] = prank.extras_for(conn, rid, card=view["card"]) if view["gate"] == "ok" else \
        prank.extras_for(conn, rid)
    view["pending"] = len(coach.pending(conn, rid))
    view["coach"] = coach.status(rid)
    t = clock.today()
    view["unanswered_cards"] = [
        {"day": d, "title": engine.session_card(conn, engine.runner(conn, rid), d,
                                                engine.plan(conn, rid).get(d))["title"],
         "km": engine.plan(conn, rid)[d]["distance_km"]}
        for d in view["unanswered"]]
    view["clock"] = {"iso": clock.now().isoformat(), "weekday": t.strftime("%A")}
    _maybe_sunday_review(conn, rid)
    return view


@app.get("/api/day")
def day(rid: int, day: str, conn=Depends(get_conn)):
    r = need_runner(conn, rid)
    if engine.screening_state(conn, rid) != "ok":
        raise HTTPException(403, "Answer the health questions first.")
    d = clock.parse(day).isoformat()
    card = engine.session_card(conn, r, d, engine.plan(conn, rid).get(d))
    log = engine.logs_by_day(conn, rid).get(d)
    extras = [x for x in engine.extra_runs(conn, rid) if x["day"] == d]
    return {"card": card, "log": log, "extra_runs": extras, "is_today": d == clock.today().isoformat(),
            "is_past": d < clock.today().isoformat()}


@app.get("/api/status")
def status(rid: int | None = None):
    return {"online": coach.online(), "coach": coach.status(rid) if rid else None}


# ---------------- logging ----------------

class LogIn(BaseModel):
    rid: int
    day: str | None = None
    status: str                 # done | partial | missed
    distance_km: float | None = None
    minutes: float | None = None
    effort: int | None = None
    pain: str = "none"          # none | soreness | sharp
    pain_where: str | None = None
    sleep: str | None = None
    note: str | None = None
    extra: bool = False         # a run done outside the plan
    breaks: str | None = None   # none | planned | some | many


def _after_log(conn, rid: int, log: dict) -> str | None:
    """Decide whether the coach should redraft the coming days. Returns the trigger."""
    if engine.screening_state(conn, rid) != "ok":
        return None
    if log["pain"] == "sharp":
        return None  # code blocks runs at once; the rebuild waits for "pain is gone"
    if log.get("extra"):
        return f"extra run of {log['distance_km']:g} km on {log['day']} (not in the plan)"
    r = engine.runner(conn, rid)
    p = engine.plan(conn, rid).get(log["day"]) or {}
    if log["status"] == "missed" and engine.is_run(p):
        return f"missed {p['session_type']} session on {log['day']}"
    target = engine.TARGET_EFFORT.get(p.get("session_type", "easy"), 4)
    if log.get("effort") and log["effort"] >= target + 3:
        return f"run on {log['day']} felt harder than planned (effort {log['effort']}, target {target})"
    planned_km = p.get("distance_km") or 0
    if engine.is_run(p) and log.get("distance_km") is not None and log["distance_km"] < 0.75 * planned_km:
        return (f"ran {log['distance_km']:g} of {planned_km:g} km on {log['day']}"
                f"{' with many breaks' if log.get('breaks') == 'many' else ''}")
    if engine.is_run(p) and log.get("breaks") == "many":
        return f"run on {log['day']} needed many more breaks than planned"
    prog = engine.runwalk_progress(conn, r, clock.today())
    if prog and prog["unlocked"]:
        return f"run-walk step unlocked: {prog['current']} -> {prog['next']}"
    return None


@app.post("/api/log")
def save_log(body: LogIn, conn=Depends(get_conn)):
    rid = body.rid
    need_runner(conn, rid)
    d = clock.parse(body.day).isoformat() if body.day else clock.today().isoformat()
    if d > clock.today().isoformat():
        raise HTTPException(400, "You can only log today or earlier.")
    if body.status not in ("done", "partial", "missed"):
        raise HTTPException(400, "Choose done, partly or missed.")
    if body.pain not in ("none", "soreness", "sharp"):
        raise HTTPException(400, "Unknown pain answer.")
    if body.breaks not in (None, "none", "planned", "some", "many"):
        raise HTTPException(400, "Unknown breaks answer.")
    log = body.model_dump()
    if body.extra:
        if body.status not in ("done", "partial") or not body.distance_km or body.distance_km <= 0:
            return JSONResponse({"ok": False, "errors": ["Add how far you ran."]}, status_code=422)
        if body.distance_km > 42.2:
            return JSONResponse({"ok": False, "errors": ["That distance looks too long. Check the number."]},
                                status_code=422)
        if d < (clock.today() - timedelta(days=14)).isoformat():
            return JSONResponse({"ok": False, "errors": ["Extra runs can be added for the last 14 days."]},
                                status_code=422)
    errors = validator.check_r28(log)
    if errors:
        return JSONResponse({"ok": False, "errors": ["Please add how hard it felt (1–10) and how many minutes."]},
                            status_code=422)
    if body.status == "missed":
        log.update(distance_km=None, minutes=None, effort=None)
    conn.execute("""INSERT INTO session_log (runner_id, day, status, distance_km, minutes, location,
                    effort, pain, pain_where, sleep, note, logged_at, extra, breaks)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (rid, d, log["status"], log["distance_km"], log["minutes"], None, log["effort"],
                  log["pain"], log["pain_where"], log["sleep"], log["note"], clock.utc_iso(), int(body.extra),
                  None if body.status == "missed" else body.breaks))
    new_trial = _maybe_time_trial(conn, rid, d, log) if not body.extra else False
    log["day"] = d
    trigger = _after_log(conn, rid, log)
    if trigger:
        coach.start_draft(None, rid, trigger)
    pace = _pace_text(log)
    msg = f"Saved. {pace}" if pace else "Saved."
    if log["pain"] == "sharp":
        msg = ("Saved. All runs are paused until you say the pain is gone. Walking only if it is pain-free. "
               "If it is not better in 3 days or gets worse, see a doctor or physiotherapist.")
    elif body.extra:
        msg = (f"Saved your extra {body.distance_km:g} km. It counts towards your weekly total, "
               "so the coach will check the coming days.")
    elif log["status"] == "missed":
        msg = "Saved. Missed sessions are never added on top of another run."
    else:
        ex = prank.extras_for(conn, rid)
        if ex and ex.get("saved") and log["status"] == "done":
            msg = f"{ex['saved']} {pace}".strip()
    if new_trial:
        msg += " Saved as your new time trial: paces and prediction are updated."
    return {"ok": True, "message": msg, "replanning": bool(trigger)}


def _pace_text(log: dict) -> str:
    """Pace and treadmill speed are always calculated, never typed."""
    km, mins = log.get("distance_km"), log.get("minutes")
    if not km or not mins or log.get("status") == "missed":
        return ""
    p = mins / km
    return (f"{km:g} km in {engine.fmt_hms(mins * 60).lstrip('0:') or '0'}, "
            f"{engine.fmt_pace(p)} /km ({engine.fmt_speed(p)} km/h).")


def _maybe_time_trial(conn, rid: int, day: str, log: dict) -> bool:
    """A planned time-trial session, done in full, becomes the new time trial (R-25)."""
    p = engine.plan(conn, rid).get(day) or {}
    if p.get("session_type") != "time_trial" or log["status"] != "done" or not log.get("distance_km"):
        return False
    r = engine.runner(conn, rid)
    loc = p.get("location")
    setting = "treadmill_1pct" if loc == "treadmill" and r["has_treadmill"] else "outdoor_heat"
    conn.execute("INSERT INTO time_trial (runner_id, day, distance_km, time_s, setting) VALUES (?,?,?,?,?)",
                 (rid, day, log["distance_km"], round(log["minutes"] * 60), setting))
    return True


class PainIn(BaseModel):
    rid: int
    pain: str = "sharp"
    pain_where: str | None = None


@app.post("/api/pain")
def report_pain(body: PainIn, conn=Depends(get_conn)):
    """'I have pain' on Today. Sharp pain or limping pauses all runs (R-07)."""
    need_runner(conn, body.rid)
    if body.pain not in ("soreness", "sharp"):
        raise HTTPException(400, "Choose normal soreness or sharp pain.")
    t = clock.today().isoformat()
    existing = db.row(conn, "SELECT id FROM session_log WHERE runner_id=? AND day=? ORDER BY id DESC LIMIT 1",
                      body.rid, t)
    if existing:
        conn.execute("UPDATE session_log SET pain=?, pain_where=? WHERE id=?",
                     (body.pain, body.pain_where, existing["id"]))
    else:
        conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, pain_where, logged_at)
                        VALUES (?,?,'missed',?,?,?)""", (body.rid, t, body.pain, body.pain_where, clock.utc_iso()))
    if body.pain == "sharp":
        return {"message": "All runs are paused until you say the pain is gone. Walking only if it is "
                           "pain-free. If it is not better in 3 days or gets worse, see a doctor or physiotherapist."}
    return {"message": "Noted. Normal soreness is fine; take it easy and see how tomorrow feels."}


class Rid(BaseModel):
    rid: int


@app.post("/api/pain/gone")
def pain_gone(body: Rid, conn=Depends(get_conn)):
    need_runner(conn, body.rid)
    n = conn.execute("""UPDATE session_log SET pain_resolved_at=? WHERE runner_id=? AND pain='sharp'
                        AND pain_resolved_at IS NULL""", (clock.utc_iso(), body.rid)).rowcount
    if n:
        coach.start_draft(None, body.rid, "pain gone, rebuild gently")
    return {"message": "Glad it's gone. Runs are back, and the coach will rebuild gently." if n
            else "There was no open pain."}


class OkRun(BaseModel):
    rid: int
    day: str


@app.post("/api/plan/ok")
def ok_run(body: OkRun, conn=Depends(get_conn)):
    """The runner's explicit OK for an elevated-risk run (R-04)."""
    need_runner(conn, body.rid)
    p = db.row(conn, "SELECT * FROM plan_day WHERE runner_id=? AND day=?", body.rid, body.day)
    if not p or not p["elevated_risk"]:
        raise HTTPException(400, "This run does not need an OK.")
    conn.execute("UPDATE plan_day SET runner_ok_at=? WHERE runner_id=? AND day=?",
                 (clock.utc_iso(), body.rid, body.day))
    return {"ok": True}


# ---------------- week and calendar ----------------

@app.get("/api/plan")
def plan_days(rid: int, conn=Depends(get_conn)):
    need_runner(conn, rid)
    if engine.screening_state(conn, rid) != "ok":
        raise HTTPException(403, "Answer the health questions first.")
    plan = engine.plan(conn, rid)
    lg = engine.logs_by_day(conn, rid)
    extra = engine.extra_km_by_day(conn, rid)
    t = clock.today().isoformat()
    ex = prank.extras_for(conn, rid, card=engine.today_view(conn, rid)["card"])
    out = []
    for d, p in plan.items():
        doc = engine._steps_doc(p)
        item = {"day": d, "type": p["session_type"], "label": engine.TYPE_LABEL[p["session_type"]],
                "title": doc.get("title") or engine.TYPE_LABEL[p["session_type"]],
                "km": p["distance_km"], "run_walk": p["run_walk"],
                "location": p["location"] if p["location"] != "treadmill" or engine.runner(conn, rid)["has_treadmill"]
                else "outdoor",
                "elevated": bool(p["elevated_risk"]), "ok": bool(p["runner_ok_at"]),
                "log": None, "extra_km": extra.get(d, 0)}
        if d in lg:
            x = lg[d]
            item["log"] = {k: x[k] for k in ("status", "distance_km", "minutes", "effort", "pain")}
        if d == t and ex and ex.get("big_day"):  # display only; the stored plan is untouched
            item["km"] = ex["big_day"]["km"]
            item["title"] = ex["big_day"]["title"]
            item["type"], item["label"] = "long", engine.TYPE_LABEL["long"]
        out.append(item)
    for d in (set(extra) | set(lg)) - set(plan):  # runs logged on days outside the plan (e.g. before setup)
        x = lg.get(d)
        out.append({"day": d, "type": "easy" if x else "rest", "label": "Run" if x else "Rest",
                    "title": (x.get("note") or "Run before the plan") if x else "Rest", "km": 0,
                    "run_walk": None, "location": "none", "elevated": False, "ok": False,
                    "log": {k: x[k] for k in ("status", "distance_km", "minutes", "effort", "pain")} if x else None,
                    "extra_km": extra.get(d, 0)})
    out.sort(key=lambda x: x["day"])
    return {"today": t, "race_day": engine.runner(conn, rid)["race_day"], "days": out,
            "weekly": _weekly_totals(conn, rid)}


def _weekly_totals(conn, rid):
    plan = engine.plan(conn, rid)
    if not plan:
        return []
    runs_plan = {d: float(p["distance_km"]) for d, p in plan.items()
                 if engine.is_run(p) and p["session_type"] != "race"}
    first, last = clock.parse(min(plan)), clock.parse(max(plan))
    out = []
    m = clock.week_start(first)
    while m <= last:
        out.append({"week": m.isoformat(), "km": engine.week_total(runs_plan, m)})
        m += timedelta(days=7)
    return out


@app.get("/api/export.ics")
def export_ics(rid: int, conn=Depends(get_conn)):
    """Calendar export from the stored plan only (prank mode never reaches it)."""
    r = need_runner(conn, rid)
    if engine.screening_state(conn, rid) != "ok":
        raise HTTPException(403, "Answer the health questions first.")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Race Coach//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{r['name']} half marathon"]
    stamp = clock.now_utc().strftime("%Y%m%dT%H%M%SZ")
    for d, p in engine.plan(conn, rid).items():
        if p["session_type"] == "rest":
            continue
        card = engine.session_card(conn, r, d, p)
        title = card["title"] if card["title"] != card["type_label"] else card["type_label"]
        if p["distance_km"] and f"{p['distance_km']:g} km" not in title:
            title += f" {p['distance_km']:g} km"
        desc = "\\n".join([*card["steps"], card["heat"] or "", card["water"] or ""]).replace(",", "\\,")
        start = clock.parse(d)
        lines += ["BEGIN:VEVENT", f"UID:{rid}-{d}@race-coach", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{start + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{title.replace(',', '')}", f"DESCRIPTION:{desc}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(body, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{r["name"].lower()}-plan.ics"'})


# ---------------- tell the coach and review changes ----------------

class Tell(BaseModel):
    rid: int
    text: str


@app.post("/api/tell")
def tell(body: Tell, conn=Depends(get_conn)):
    need_runner(conn, body.rid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Type a message first.")
    return coach.handle_note(conn, body.rid, text[:500])


@app.get("/api/notes")
def notes(rid: int, conn=Depends(get_conn)):
    return db.rows(conn, "SELECT created_at, text, reply FROM coach_note WHERE runner_id=? ORDER BY id DESC LIMIT 8", rid)


@app.get("/api/proposals")
def proposals(rid: int, conn=Depends(get_conn)):
    r = need_runner(conn, rid)
    plan = engine.plan(conn, rid)
    out = []
    for p in coach.pending(conn, rid):
        d = p["draft"]
        changes = []
        for c in d.get("changes", []):
            old = plan.get(c["day"])
            changes.append({
                "day": c["day"],
                "old": _short(old),
                "new": {"type": c["session_type"], "label": engine.TYPE_LABEL[c["session_type"]],
                        "km": c.get("distance_km") or 0, "run_walk": c.get("run_walk")},
                "reason": c.get("reason"), "rule_ids": c.get("rule_ids") or [],
                "elevated": (d.get("elevated") or {}).get(c["day"]),
            })
        out.append({"id": p["id"], "created_at": p["created_at"], "trigger": p["trigger"],
                    "summary": d.get("summary"), "fallback": p["model"] == "fallback", "changes": changes})
    return {"proposals": out, "rule_names": {x["id"]: x.get("short") or x["text"][:70] for x in rules.load()}}


def _short(p):
    if not p:
        return {"type": "rest", "label": "Rest", "km": 0, "run_walk": None}
    return {"type": p["session_type"], "label": engine.TYPE_LABEL[p["session_type"]],
            "km": p["distance_km"], "run_walk": p["run_walk"]}


class Decide(BaseModel):
    rid: int
    accept: bool
    oks: list[str] = []


@app.post("/api/proposals/{pid}/decide")
def decide(pid: int, body: Decide, conn=Depends(get_conn)):
    need_runner(conn, body.rid)
    return coach.decide(conn, body.rid, pid, body.accept, set(body.oks))


# ---------------- race ----------------

@app.get("/api/race")
def race(rid: int, conn=Depends(get_conn)):
    r = need_runner(conn, rid)
    pred = engine.prediction(conn, r)
    pc = engine.paces(conn, r)
    if pred:
        pred = {k: v for k, v in pred.items() if k in ("kind", "low", "high", "formula", "ratio", "rules")}
    zones = None
    if not pc.get("run_walk"):
        zones = {k: {"pace": v["pace"], "speed": v["speed"]} for k, v in pc.get("zones", {}).items()}
    t = clock.today()
    next_tt = next((d for d, p in engine.plan(conn, rid).items()
                    if p["session_type"] == "time_trial" and d >= t.isoformat()), None)
    return {"race_day": r["race_day"], "days_to_race": (clock.parse(r["race_day"]) - t).days,
            "next_time_trial": next_tt,
            "prediction": pred, "goal_time": engine.fmt_hms(r["goal_time_s"], True) if r["goal_time_s"] else None,
            "pace_band": engine.pace_band(conn, r), "zones": zones,
            "run_walk": bool(r["run_walk"]), "guide": pc if pc.get("run_walk") else None,
            "time_trials": [{**x, "time": engine.fmt_hms(x["time_s"])} for x in
                            db.rows(conn, "SELECT * FROM time_trial WHERE runner_id=? ORDER BY day DESC", rid)]}


class Trial(BaseModel):
    rid: int
    day: str
    distance_km: float = 5
    time: str                  # "mm:ss" or "h:mm:ss"
    setting: str


@app.post("/api/time_trial")
def add_trial(body: Trial, conn=Depends(get_conn)):
    need_runner(conn, body.rid)
    if body.setting not in ("treadmill_1pct", "treadmill_flat", "outdoor_heat", "outdoor_cool"):
        raise HTTPException(400, "Unknown setting")
    parts = [int(x) for x in body.time.strip().split(":")]
    secs = parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) == 3 else 0)
    if not 0 < body.distance_km <= 21.1 or secs < 60 * body.distance_km * 2.5:
        raise HTTPException(400, "That time looks too fast. Check minutes and seconds.")
    d = clock.parse(body.day).isoformat()
    if d > clock.today().isoformat():
        raise HTTPException(400, "A time trial can only be today or earlier.")
    conn.execute("INSERT INTO time_trial (runner_id, day, distance_km, time_s, setting) VALUES (?,?,?,?,?)",
                 (body.rid, d, body.distance_km, secs, body.setting))
    return race(body.rid, conn)


# ---------------- prank mode (display only) ----------------

class Knock(BaseModel):
    rid: int
    taps: int


@app.post("/api/knock")
def knock(body: Knock, conn=Depends(get_conn)):
    need_runner(conn, body.rid)
    prank.knock(conn, body.rid, body.taps)
    return Response(status_code=204)


# ---------------- backups ----------------

@app.get("/api/backup")
def export_backup(conn=Depends(get_conn)):
    tmp = Path(tempfile.mkdtemp()) / f"race-coach-backup-{clock.today().isoformat()}.db"
    db.backup(conn, tag="export", dest=tmp)
    return FileResponse(tmp, filename=tmp.name, media_type="application/octet-stream")


@app.exception_handler(ValueError)
def bad_value(request: Request, exc: ValueError):
    return JSONResponse({"detail": "That doesn't look right. Check the date or number and try again."},
                        status_code=400)


# ---------------- start ----------------

def prepare(conn=None):
    """Back up, migrate, and import the seed plans the first time."""
    conn = conn or db.connect()
    db.migrate(conn)
    db.daily_backup(conn)
    if not db.row(conn, "SELECT id FROM runner LIMIT 1"):
        for f in seed.SEEDS:
            res = seed.import_seed(conn, seed.load(f))
            if not res["ok"]:
                print(f"Seed {f} not imported:", *res["failures"], sep="\n  ")
    return conn


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((config.HOST, port)) == 0


def main():
    import uvicorn
    url = f"http://{config.HOST}:{config.PORT}/"
    if _port_in_use(config.PORT):  # already running: just open it
        webbrowser.open(url)
        return
    prepare().close()
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
