"""Ollama client, prompts and output schema. The model drafts; the validator decides.
If Ollama is off or a draft fails twice, the safe fallback runs instead."""
import json
import re
import threading
from datetime import date, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import clock, config, engine, rules, validator
from .db import row, rows

# ---------------- schemas ----------------


class DayChange(BaseModel):
    day: date
    session_type: Literal["rest", "walk", "easy", "long", "tempo",
                          "intervals", "race_pace", "strength", "time_trial"]
    distance_km: float = Field(ge=0, le=21)
    zone: Literal["none", "walk", "easy", "long", "race", "tempo", "intervals"]
    location: Literal["treadmill", "outdoor", "either", "gym", "none"]
    run_walk: Literal["continuous", "1:1", "90/60", "2:1", "3:1", "4:1", "none"]
    rule_ids: list[str] = Field(min_length=1)
    reason: str = Field(max_length=160)


class Proposal(BaseModel):
    summary: str = Field(max_length=300)
    changes: list[DayChange] = Field(max_length=14)


class NoteRecord(BaseModel):
    kind: Literal["travel", "sick", "better", "pain", "pain_gone", "missed", "felt_hard",
                  "question", "request", "other"]
    start_day: date | None = None
    end_day: date | None = None
    can_run: bool | None = None
    climate: Literal["hot", "cool", "unknown"] = "unknown"
    short: str = Field(default="", max_length=120)


# ---------------- status shared with the screens ----------------

_status: dict[int, dict] = {}
_lock = threading.Lock()


def status(rid: int) -> dict:
    with _lock:
        return dict(_status.get(rid, {"state": "idle"}))


def _set(rid: int, **kw):
    with _lock:
        _status[rid] = kw


# ---------------- ollama ----------------

def online() -> bool:
    try:
        r = httpx.get(f"{config.OLLAMA_HOST}/api/tags", timeout=2)
        names = [m["name"] for m in r.json().get("models", [])]
        return r.status_code == 200 and any(n.startswith(config.MODEL.split(":")[0]) for n in names)
    except Exception:
        return False


def _chat(system: str, user: str, schema: dict, timeout: float = 600) -> str:
    from ollama import Client
    client = Client(host=config.OLLAMA_HOST, timeout=timeout)
    resp = client.chat(model=config.MODEL,
                       messages=[{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                       format=schema, options=dict(config.MODEL_OPTIONS))
    return resp.message.content


chat = _chat  # tests replace this


# ---------------- "Tell the coach" reader ----------------

READER_SYSTEM = """You turn a runner's short message into a small JSON record. Do not give advice.
kind: travel (a trip), sick (ill now), better (no longer sick), pain (pain or injury now),
pain_gone (pain has gone), missed (did not do a session), felt_hard (a run felt too hard),
question (asks something), request (asks to change the plan, e.g. a longer run), other.
start_day/end_day: ISO dates if the message gives dates; today is {today} ({weekday}), year 2026.
can_run: for travel, whether they will run on the trip; otherwise null.
climate: cool if the trip is somewhere cool (e.g. Japan in November), hot if hot, else unknown.
short: the message restated in under 15 words."""

SYMPTOM = re.compile(r"chest|dizz|faint|breath|heart|palpitat|swell|swollen|numb|blood|"
                     r"injur|pain|hurt|ache|sore|knee|ankle|shin|hip|back|calf|foot|feet|"
                     r"fever|cough|sick|ill|medicine|tablet|doctor", re.I)
DOCTOR_LINE = "I can't give medical advice. Please see a doctor or physiotherapist about this."


def read_note_rules(text: str, today: date) -> NoteRecord:
    """Simple keyword reader used when the model is offline."""
    t = text.lower()
    if re.search(r"(no longer|not) (sick|ill)|feel(ing)? better|recovered", t):
        kind = "better"
    elif re.search(r"pain (is )?gone|no (more )?pain|pain.*(better|gone)", t):
        kind = "pain_gone"
    elif re.search(r"sharp|limp|injur|pain|hurt", t):
        kind = "pain"
    elif re.search(r"\bsick\b|\bill\b|fever|flu|cold\b|covid", t):
        kind = "sick"
    elif re.search(r"travel|trip|going to|flying|holiday|japan", t):
        kind = "travel"
    elif re.search(r"miss|couldn'?t|could not|skipped|didn'?t run", t):
        kind = "missed"
    elif re.search(r"(very |too |really )?hard|exhaust|tough|struggl", t):
        kind = "felt_hard"
    elif re.search(r"give me|can i (do|run)|make .*(longer|shorter)|change|move|swap|\d+\s*km", t):
        kind = "request"
    elif t.strip().endswith("?"):
        kind = "question"
    else:
        kind = "other"
    start = end = None
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                          "sep", "oct", "nov", "dec"], 1)}
    found = re.findall(r"(\d{1,2})\s*([a-z]{3})?[a-z]*", t)
    ds = []
    last_month = None
    for dnum, mon in reversed(found):
        if mon in months:
            last_month = months[mon]
        if last_month and 1 <= int(dnum) <= 31:
            try:
                ds.append(date(2026, last_month, int(dnum)))
            except ValueError:
                pass
    ds = sorted(ds)
    if ds:
        start, end = ds[0], ds[-1]
    can_run = None
    if kind == "travel":
        can_run = not re.search(r"no running|not run|won'?t run|no run", t)
    climate = "cool" if re.search(r"japan|cool|cold|winter|korea|europe|uk\b", t) else "unknown"
    return NoteRecord(kind=kind, start_day=start, end_day=end, can_run=can_run,
                      climate=climate, short=text[:120])


def read_note(text: str, today: date) -> NoteRecord:
    if online():
        try:
            out = chat(READER_SYSTEM.format(today=today.isoformat(), weekday=today.strftime("%A")),
                       text, NoteRecord.model_json_schema(), timeout=180)
            return NoteRecord.model_validate_json(out)
        except Exception:
            pass
    return read_note_rules(text, today)


def handle_note(conn, rid: int, text: str, today: date | None = None, background: bool = True) -> dict:
    """Code saves what the reader found; it never edits the plan itself."""
    today = today or clock.today()
    rec = read_note(text, today)
    reply, trigger = "Noted.", None
    t = today.isoformat()
    if rec.kind == "travel":
        s = (rec.start_day or today).isoformat()
        e = (rec.end_day or rec.start_day or today).isoformat()
        can = 1 if rec.can_run else 0
        conn.execute("""INSERT INTO availability (runner_id, start_day, end_day, kind, can_run, climate, note)
                        VALUES (?,?,?,?,?,?,?)""",
                     (rid, s, e, "travel", can, None if rec.climate == "unknown" else rec.climate, rec.short or text[:80]))
        reply = f"Saved your trip, {s} to {e}, " + ("with running." if can else "with no running.")
        trigger = f"travel {s}..{e} can_run={bool(can)} climate={rec.climate}"
    elif rec.kind == "sick":
        s = (rec.start_day or today).isoformat()
        end = (clock.parse(engine.runner(conn, rid)["race_day"]) - timedelta(days=1)).isoformat()
        conn.execute("""INSERT INTO availability (runner_id, start_day, end_day, kind, can_run, note)
                        VALUES (?,?,?,?,0,?)""", (rid, s, end, "sick", text[:80]))
        reply = "Rest until you feel better, then tell me “I'm better”. " + DOCTOR_LINE
    elif rec.kind == "better":
        y = (today - timedelta(days=1)).isoformat()
        conn.execute("UPDATE availability SET end_day=? WHERE runner_id=? AND kind='sick' AND end_day>=?",
                     (y, rid, t))
        reply = "Glad you're better. The coach will rebuild the coming days."
        trigger = "back from being sick"
    elif rec.kind == "pain":
        reply = ("Sorry about the pain. Tap “I have pain” on Today so all runs pause. "
                 "If it is not better in 3 days or gets worse, see a doctor or physiotherapist.")
    elif rec.kind == "pain_gone":
        n = conn.execute("UPDATE session_log SET pain_resolved_at=? WHERE runner_id=? AND pain='sharp' "
                         "AND pain_resolved_at IS NULL", (clock.utc_iso(), rid)).rowcount
        reply = "Good to hear. Runs are unpaused and the coach will rebuild gently." if n else "Noted. There was no open pain."
        trigger = "pain gone, rebuild" if n else None
    elif rec.kind == "missed":
        d = (rec.start_day or today).isoformat()
        if not row(conn, "SELECT id FROM session_log WHERE runner_id=? AND day=?", rid, d):
            conn.execute("""INSERT INTO session_log (runner_id, day, status, logged_at)
                            VALUES (?,?,'missed',?)""", (rid, d, clock.utc_iso()))
        reply = "No problem. Missed sessions are never added on top of another run."
        trigger = f"missed session on {d}"
    elif rec.kind == "felt_hard":
        reply = "Thanks for telling me. The coach will check whether the next days should be easier."
        trigger = f"runner said: {text[:120]}"
    elif rec.kind == "request":
        reply = "The coach will look at that. Every change still goes through the safety checks."
        trigger = f"runner request: {text[:160]}"
    elif rec.kind == "question":
        reply = DOCTOR_LINE if SYMPTOM.search(text) else \
            "I only change the plan. Ask me things like “Going to Japan 31 Oct–9 Nov, no running”."
    conn.execute("INSERT INTO coach_note (runner_id, created_at, text, record_json, reply) VALUES (?,?,?,?,?)",
                 (rid, clock.utc_iso(), text, rec.model_dump_json(), reply))
    if trigger and engine.screening_state(conn, rid) == "ok":
        start_draft(conn, rid, trigger, runner_request=rec.kind == "request", background=background)
        reply += " Working on your new plan…"
    return {"record": json.loads(rec.model_dump_json()), "reply": reply}


# ---------------- plan drafter ----------------

DRAFTER_SYSTEM = """You are a careful running coach for a beginner half-marathon plan.
Limits: no medical advice; change only what the trigger needs; keep easy days easy.
Never add a missed session on top of another run; drop it or move it to a free day.
Never write paces, speeds, drink amounts or foods: code adds those.
Only change days from {start} to {end}. Only output days that change.
Each change must cite at least one rule ID from the lists below.

Rules (SHOULD / MAY), word for word:
{should}

These MUST rules will be checked by code; a draft that breaks one is rejected:
{must}"""


def build_context(conn, rid: int, trigger: str, today: date) -> dict:
    r = engine.runner(conn, rid)
    plan = engine.plan(conn, rid)
    lg = engine.logs(conn, rid)
    end = today + timedelta(days=14)
    pred = engine.prediction(conn, r)
    prog = engine.runwalk_progress(conn, r, today)
    hold = engine.hard_hold(conn, rid, today)
    return {
        "profile": {"name": r["name"], "level": r["level"], "run_walk": bool(r["run_walk"]),
                    "treadmill": bool(r["has_treadmill"]), "prior_injury": bool(r["prior_injury"]),
                    "diet": r["diet"]},
        "today_singapore": today.isoformat(), "weekday": today.strftime("%A"),
        "race_day": r["race_day"],
        "travel_and_sick": [{k: a[k] for k in ("start_day", "end_day", "kind", "can_run", "climate", "note")}
                            for a in engine.availability(conn, rid) if a["end_day"] >= today.isoformat()],
        "next_14_days": [{k: p[k] for k in ("day", "session_type", "distance_km", "location", "run_walk")}
                         for d, p in plan.items() if today.isoformat() < d <= end.isoformat()],
        "last_21_days_logs": [{k: x[k] for k in ("day", "status", "distance_km", "minutes", "effort", "pain", "sleep")}
                              for x in lg if x["day"] >= (today - timedelta(days=21)).isoformat()],
        "facts": {
            "weekly_km_last_4_weeks": engine.weekly_km(conn, rid, today),
            "longest_run_last_30_days_km": engine.longest_30(conn, rid, today),
            "prediction": f"{pred['low']} to {pred['high']}" if pred else None,
            "pain_lock": bool(engine.open_pain(conn, rid)),
            "hard_run_hold_until": hold["until"] if hold else None,
            "run_walk_progress": prog,
        },
        "trigger": trigger,
    }


def default_steps(r: dict, c: dict) -> list[str]:
    st, km = c["session_type"], c["distance_km"]
    rw = engine.runwalk_text(c.get("run_walk"))
    if st == "rest":
        return ["Full rest. Walking is fine."]
    if st == "strength":
        return ["30 minutes of strength: squats, lunges, calf raises, planks.", "Stop before you strain."]
    if st == "walk":
        return [f"Brisk walk {km:g} km." if km else "Brisk walk 30–45 minutes."]
    main = f"{rw}, repeat until {km:g} km." if rw else f"{engine.TYPE_LABEL[st]}: {km:g} km at the effort below."
    return [main.capitalize() if rw else main]


def _draft_days(today: date, r: dict) -> tuple[date, date]:
    race = clock.parse(r["race_day"])
    start = today + timedelta(days=1)
    end = min(today + timedelta(days=14), race - timedelta(days=1))
    return start, end


def _to_changes(conn, rid, proposal: Proposal, today) -> list[dict]:
    r = engine.runner(conn, rid)
    plan = engine.plan(conn, rid)
    out = []
    for ch in proposal.changes:
        c = ch.model_dump()
        c["day"] = c["day"].isoformat()
        cur = plan.get(c["day"])
        same = cur and cur["session_type"] == c["session_type"] and \
            abs((cur["distance_km"] or 0) - c["distance_km"]) < 0.01 and \
            (cur["run_walk"] or "none") in (c["run_walk"], "continuous" if c["run_walk"] == "none" else "")
        if same:
            continue
        if c["session_type"] in ("rest", "strength"):
            c["distance_km"] = 0.0
        if c["location"] == "treadmill" and not r["has_treadmill"]:
            c["location"] = "outdoor"
        c["title"] = engine.TYPE_LABEL[c["session_type"]]
        c["steps"] = default_steps(r, c)
        out.append(c)
    return out


def _save_proposal(conn, rid, trigger, attempt, draft, checks, status, model=config.MODEL) -> int:
    cur = conn.execute("""INSERT INTO proposal (runner_id, created_at, trigger, model, attempt,
                          draft_json, checks_json, status) VALUES (?,?,?,?,?,?,?,?)""",
                       (rid, clock.utc_iso(), trigger, model, attempt, json.dumps(draft),
                        json.dumps(checks), status))
    return cur.lastrowid


def draft(conn, rid: int, trigger: str, *, runner_request=False, today: date | None = None) -> dict:
    today = today or clock.today()
    r = engine.runner(conn, rid)
    if engine.screening_state(conn, rid) != "ok":
        return {"status": "locked"}
    if not online():
        return fallback(conn, rid, trigger, today=today, why="Coach offline")
    start, end = _draft_days(today, r)
    if start > end:
        return {"status": "nothing", "message": "Race week: no new changes."}
    system = DRAFTER_SYSTEM.format(start=start, end=end, should=rules.prompt_text(),
                                   must="\n".join(f"{x['id']}: {x['text']}" for x in rules.by_strength("MUST")))
    ctx = build_context(conn, rid, trigger, today)
    user = json.dumps(ctx, default=str)
    feedback = None
    for attempt in (1, 2):
        msg = user if not feedback else user + "\n\nYour last draft failed these checks; fix them:\n" + feedback
        try:
            raw = chat(system, msg, Proposal.model_json_schema())
            prop = Proposal.model_validate_json(raw)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            _save_proposal(conn, rid, trigger, attempt, {"raw_error": str(e)[:500]},
                           [{"rule": "shape", "ok": False, "failures": ["reply did not match the schema"]}],
                           "failed_checks")
            feedback = "The reply did not match the JSON schema."
            continue
        except Exception as e:  # connection problems
            return fallback(conn, rid, trigger, today=today, why=f"Coach offline ({type(e).__name__})")
        changes = _to_changes(conn, rid, prop, today)
        res = validator.validate(conn, rid, changes, today=today, texts=[prop.summary],
                                 runner_request=runner_request)
        draft_doc = {"summary": prop.summary, "changes": changes}
        if res["ok"]:
            if not changes:
                pid = _save_proposal(conn, rid, trigger, attempt, draft_doc, res["checks"], "accepted")
                return {"status": "no_change", "proposal_id": pid, "summary": prop.summary}
            draft_doc["elevated"] = res["elevated"]
            pid = _save_proposal(conn, rid, trigger, attempt, draft_doc, res["checks"], "awaiting_runner")
            return {"status": "awaiting_runner", "proposal_id": pid}
        _save_proposal(conn, rid, trigger, attempt, draft_doc, res["checks"], "failed_checks")
        feedback = "\n".join(res["failures"][:12])
    out = fallback(conn, rid, trigger, today=today, why="The coach's drafts failed the safety checks twice")
    if runner_request:
        out["explain"] = feedback
    return out


# ---------------- fallback without the model ----------------

def _round_half(x: float) -> float:
    return max(0.5, round(x * 2) / 2)


def fallback(conn, rid: int, trigger: str, *, today: date | None = None, why: str = "") -> dict:
    """Current plan stays; missed sessions are dropped; no distance goes up.
    After 7+ days off, the first week back repeats the last full week before the
    break at one step lower (R-33)."""
    today = today or clock.today()
    r = engine.runner(conn, rid)
    plan = engine.plan(conn, rid)
    lg = engine.logs_by_day(conn, rid)
    start, end = _draft_days(today, r)
    changes: list[dict] = []
    ran = sorted({d for d, x in lg.items() if x["status"] != "missed" and (x["distance_km"] or x["minutes"])}
                 | set(engine.extra_km_by_day(conn, rid)))
    last = ran[-1] if ran else None
    if start <= end and last and clock.days_between(last, today) >= 7:
        lw = clock.week_start(clock.parse(last))
        week_end = min(end, start + timedelta(days=6))
        for d in clock.daterange(start, week_end):
            src = (lw + timedelta(days=d.weekday())).isoformat()
            p = plan.get(src)
            if not engine.is_run(p):
                continue
            c = {"day": d.isoformat(), "session_type": "easy" if p["session_type"] in engine.HARD_TYPES else p["session_type"],
                 "zone": None, "location": "outdoor" if not r["has_treadmill"] else (p["location"] or "either"),
                 "rule_ids": ["R-33"], "reason": "First week back: last full week before the break, one step lower."}
            if r["run_walk"] and p["run_walk"] in engine.RW_STEPS:
                c["run_walk"] = engine.RW_STEPS[max(0, engine.RW_STEPS.index(p["run_walk"]) - 1)]
                c["distance_km"] = p["distance_km"]
            else:
                c["run_walk"] = p["run_walk"]
                c["distance_km"] = _round_half(p["distance_km"] * 0.8)
            c["steps"] = default_steps(r, c)
            changes.append(c)
        # clear runs that are not in the rebuilt week
        rebuilt = {c["day"] for c in changes}
        for d in clock.daterange(start, week_end):
            k = d.isoformat()
            if engine.is_run(plan.get(k)) and k not in rebuilt:
                changes.append({"day": k, "session_type": "rest", "distance_km": 0, "location": "none",
                                "run_walk": None, "rule_ids": ["R-33"], "reason": "Rest in the first week back.",
                                "steps": ["Full rest. Walking is fine."]})
    elif start <= end:
        # Trim the coming week until it passes the caps. Distances only go down.
        upcoming = [p for d, p in plan.items() if start.isoformat() <= d <= end.isoformat() and engine.is_run(p)
                    and p["session_type"] != "race"]
        res = validator.validate(conn, rid, [_as_change(p) for p in upcoming], today=today) if upcoming else {"ok": True}
        factor = 1.0
        while not res["ok"] and factor > 0.5:
            factor -= 0.1
            trial = []
            for p in upcoming:
                c = _as_change(p)
                c["distance_km"] = min(p["distance_km"], _round_half(p["distance_km"] * factor))
                c["rule_ids"] = ["R-03", "R-05"]
                c["reason"] = "Shortened so the week stays within safe limits."
                trial.append(c)
            res = validator.validate(conn, rid, trial, today=today)
            if res["ok"]:
                changes = [c for c, p in zip(trial, upcoming) if c["distance_km"] < p["distance_km"]]
    if changes:
        res = validator.validate(conn, rid, changes, today=today)
        if res["ok"]:
            pid = _save_proposal(conn, rid, trigger, 0, {"summary": f"{why}. A safe, simpler plan instead.",
                                                         "changes": changes, "elevated": res["elevated"]},
                                 res["checks"], "awaiting_runner", model="fallback")
            return {"status": "awaiting_runner", "proposal_id": pid, "fallback": True, "why": why}
    pid = _save_proposal(conn, rid, trigger, 0, {"summary": f"{why}. Your plan stays as it is.", "changes": []},
                         [], "fallback", model="fallback")
    return {"status": "fallback", "proposal_id": pid, "why": why}


def _as_change(p: dict) -> dict:
    doc = json.loads(p.get("steps_json") or "{}")
    return {"day": p["day"], "session_type": p["session_type"], "distance_km": p["distance_km"],
            "zone": p["zone"], "location": p["location"], "run_walk": p["run_walk"],
            "rule_ids": json.loads(p.get("rule_ids") or "[]") or ["R-05"],
            "title": doc.get("title"), "steps": doc.get("steps"), "breaks": doc.get("breaks")}


# ---------------- background runner ----------------

def start_draft(conn_or_none, rid: int, trigger: str, *, runner_request=False, background=True):
    """Never make logging wait for the model: drafts run in a background thread."""
    from . import db

    def work():
        conn = db.connect()
        _set(rid, state="working", trigger=trigger)
        try:
            out = draft(conn, rid, trigger, runner_request=runner_request)
            _set(rid, state="done", result=out)
        except Exception as e:
            out = fallback(conn, rid, trigger, why=f"Coach error ({type(e).__name__})")
            _set(rid, state="done", result=out)
        finally:
            conn.close()

    if status(rid).get("state") == "working":
        return
    if background:
        threading.Thread(target=work, daemon=True).start()
    else:
        out = draft(conn_or_none, rid, trigger, runner_request=runner_request)
        _set(rid, state="done", result=out)
        return out


def pending(conn, rid: int) -> list[dict]:
    out = rows(conn, "SELECT * FROM proposal WHERE runner_id=? AND status='awaiting_runner' ORDER BY id DESC", rid)
    for p in out:
        p["draft"] = json.loads(p["draft_json"])
    return out


def decide(conn, rid: int, pid: int, accept: bool, oks: set[str] | None = None) -> dict:
    p = row(conn, "SELECT * FROM proposal WHERE id=? AND runner_id=?", pid, rid)
    if not p or p["status"] != "awaiting_runner":
        return {"ok": False, "error": "This change is no longer waiting."}
    if not accept:
        conn.execute("UPDATE proposal SET status='declined' WHERE id=?", (pid,))
        return {"ok": True, "status": "declined"}
    d = json.loads(p["draft_json"])
    res = validator.apply(conn, rid, d["changes"], proposal_id=pid,
                          source="fallback" if p["model"] == "fallback" else "model", oks=oks)
    if res.get("applied"):
        conn.execute("UPDATE proposal SET status='accepted' WHERE id=?", (pid,))
        return {"ok": True, "status": "accepted"}
    return {"ok": False, "needs_ok": res.get("needs_ok"), "failures": res["failures"]}
