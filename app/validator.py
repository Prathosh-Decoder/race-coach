"""The validator: every MUST rule in rules/brief_v1.yaml as plain code.
It is the only module that writes plan changes to the database (apply()).
A draft that fails any check never reaches the calendar."""
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import clock, engine, rules
from .db import plan_row_json, row

RUN = engine.RUN_TYPES
HARD_OR_LONG = {"tempo", "intervals", "race_pace", "long", "time_trial"}


@dataclass
class Ctx:
    conn: object
    runner: dict
    today: date
    changes: list[dict]
    plan: dict            # merged: current plan with the changes applied
    original: dict        # current plan
    logs: dict
    avail: list
    runs: dict            # run km per day (logs for the past, merged plan for the future)
    texts: list[str]      # all model-written text (summary, reasons)
    runner_request: bool = False
    override: bool = False
    seed: bool = False
    elevated: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


# ---------------- helpers ----------------

def _week_runs_ratio(ctx, monday: date) -> list[str]:
    """R-05 for one Monday-Sunday week. Returns failure messages."""
    r = ctx.runner
    first = clock.parse(r["first_week"])
    runs = ctx.runs
    total = engine.week_total(runs, monday)
    if monday < first:
        return []
    if monday == first:  # starter caps: R-03 per run (checked separately) and at most 3 runs
        n = engine.week_sessions(runs, monday)
        return [] if n <= 3 else [f"R-05 failed in the week of {monday}: {n} runs in the first week (starter cap is 3)"]
    prev = monday - timedelta(days=7)
    if prev == first and engine.week_total(runs, first) < 10:
        return []  # first week under 10 km: R-05 starts one week later
    back = _return_week(ctx, monday)
    if back:
        last_full, ok_step, why = back
        if ok_step:
            return []
        cap = 1.3 * engine.week_total(runs, last_full)
        if total <= cap + 1e-9:
            return []
        return [f"R-05 failed in the week of {monday}: {total:g} km after a break is more than "
                f"1.3 × {engine.week_total(runs, last_full):g} km and {why}"]
    before = engine.week_total(runs, prev)
    if total <= 1.3 * before + 1e-9:
        return []
    return [f"R-05 failed in the week of {monday}: {total:g} km is more than 1.3 × {before:g} km "
            f"(max {1.3 * before:.1f} km)"]


def _return_week(ctx, monday: date):
    """If this week holds the first run after 7+ days without running, return
    (last full week before the break, step-down satisfied?, explanation)."""
    runs = ctx.runs
    end = (monday + timedelta(days=6)).isoformat()
    in_week = sorted(d for d in runs if monday.isoformat() <= d <= end)
    if not in_week:
        return None
    first_run = in_week[0]
    earlier = sorted(d for d in runs if d < first_run)
    if not earlier:
        return None
    gap = clock.days_between(earlier[-1], first_run) - 1
    if gap < 7:
        return None
    last_run = clock.parse(earlier[-1])
    # last full Mon-Sun week that ended before the break began
    lw = clock.week_start(last_run)  # the week of the last run counts as the last week before the break
    r = ctx.runner
    if r["run_walk"]:
        before_ratio = ctx.plan.get(earlier[-1], {}).get("run_walk")
        week_ratios = [ctx.plan.get(d, {}).get("run_walk") for d in in_week]
        if before_ratio in engine.RW_STEPS:
            limit = max(0, engine.RW_STEPS.index(before_ratio) - 1)
            ok = all(x in engine.RW_STEPS and engine.RW_STEPS.index(x) <= limit for x in week_ratios)
            return lw, ok, f"does not start one run-walk step below {before_ratio} (R-33)"
        return lw, False, "has no run-walk step to compare (R-33)"
    total = engine.week_total(runs, monday)
    ok = total <= 0.8 * engine.week_total(runs, lw) + 1e-9
    return lw, ok, "is not about 20% below the last week before the break (R-33)"


VOLUME = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|cups?|glass(?:es)?|oz|ounces?|bottles?)\b", re.I)
DRINK = re.compile(r"drink|water|fluid|sip|hydrat|electrolyte|isotonic", re.I)
ACWR = re.compile(r"acwr|acute\s*[:/-]?\s*(?:to\s*)?[:/-]?\s*chronic", re.I)


def _changed_days(ctx) -> list[str]:
    return sorted({c["day"] for c in ctx.changes})


def _days_to_check(ctx) -> list[str]:
    """Changed days plus every planned run up to 30 days after the last change,
    because lowering one run changes the cap for the runs after it."""
    days = _changed_days(ctx)
    if not days:
        return []
    last = (clock.parse(days[-1]) + timedelta(days=30)).isoformat()
    later = [d for d in ctx.runs if days[0] <= d <= last and d > ctx.today.isoformat()]
    return sorted(set(days) | set(later))


# ---------------- the checks ----------------

def check_shape(ctx) -> list[str]:
    out = []
    race = ctx.runner["race_day"]
    seen = set()
    tomorrow = (ctx.today + timedelta(days=1)).isoformat()
    for c in ctx.changes:
        d = c["day"]
        if d in seen:
            out.append(f"{d} appears twice (never two sessions on one day)")
        seen.add(d)
        if c["session_type"] == "race" and not ctx.seed:
            out.append(f"{d}: the model cannot write race day")
        if not ctx.seed:
            if d < tomorrow:
                out.append(f"{d} is in the past or today; only tomorrow onwards can change")
            if d >= race:
                out.append(f"{d} is race day or later; only days before the race can change")
            if d in ctx.logs:
                out.append(f"{d} is already logged and cannot change")
        if c["session_type"] in RUN and c["session_type"] != "race":
            if (c.get("distance_km") or 0) <= 0:
                out.append(f"{d}: a run needs a distance")
            block = engine.covering(ctx.avail, d)
            if block and not block["can_run"]:
                out.append(f"{d} is marked 'can't run' ({block['kind']})")
            if c.get("location") == "treadmill" and not ctx.runner["has_treadmill"]:
                out.append(f"{d}: no treadmill for this runner")
    return out


def check_r03(ctx) -> list[str]:
    out = []
    base = ctx.runner["baseline_longest_km"]
    for d in _days_to_check(ctx):
        km = ctx.runs.get(d)
        if not km or ctx.plan.get(d, {}).get("session_type") == "race":
            continue
        ref = engine.longest_before(ctx.runs, d, base)
        if km > 2 * ref + 1e-9:
            out.append(f"R-03 failed on {d}: {km:g} km is more than double {ref:g} km")
        elif km > 1.1 * ref + 1e-9 and d in _changed_days(ctx):
            ctx.elevated[d] = f"{km:g} km is more than 1.1 × your longest recent run ({ref:g} km) (R-04)"
    hold = engine.hard_hold(ctx.conn, ctx.runner["id"], ctx.today) if not ctx.seed else None
    if hold:
        for c in ctx.changes:
            before = ctx.original.get(c["day"], {}).get("distance_km") or 0
            if c["day"] <= hold["until"] and (c.get("distance_km") or 0) > before:
                ctx.elevated.setdefault(c["day"], "Distance goes up within 7 days of a run that felt too hard (R-04)")
    return out


def check_r05(ctx) -> list[str]:
    days = _days_to_check(ctx)
    if not days:
        return []
    weeks = sorted({clock.week_start(clock.parse(d)) for d in days})
    weeks = sorted(set(weeks) | {w + timedelta(days=7) for w in weeks})
    race = clock.parse(ctx.runner["race_day"])
    out = []
    for w in weeks:
        if w > race:
            continue
        out += _week_runs_ratio(ctx, w)
    return out


def check_r07(ctx) -> list[str]:
    if ctx.seed:
        return []
    pain = engine.open_pain(ctx.conn, ctx.runner["id"])
    if not pain:
        return []
    return [f"R-07 failed on {c['day']}: no running while the sharp pain from {pain['day']} is open"
            for c in ctx.changes if c["session_type"] in RUN and (c.get("distance_km") or 0) > 0]


def check_r12(ctx) -> list[str]:
    race = clock.parse(ctx.runner["race_day"])
    window = {(race - timedelta(days=i)).isoformat() for i in (1, 2, 3)}
    out = []
    for d in sorted(window):
        p = ctx.plan.get(d)
        if not p or p["session_type"] not in RUN:
            continue
        if p["session_type"] in HARD_OR_LONG:
            out.append(f"R-12 failed on {d}: no {p['session_type']} in the 3 days before the race")
        if (p["distance_km"] or 0) > 5:
            out.append(f"R-12 failed on {d}: {p['distance_km']:g} km is over 5 km in the 3 days before the race")
    return out


def check_r16(ctx) -> list[str]:
    for c in ctx.changes:
        if c.get("location") == "outdoor" and c["session_type"] in RUN | {"walk"}:
            card = engine.session_card(ctx.conn, ctx.runner, c["day"], {
                **c, "steps_json": None, "rule_ids": "[]", "runner_ok_at": None, "source": "model"})
            if not card.get("heat"):
                return [f"R-16 failed on {c['day']}: the heat card is missing"]
    ctx.notes.append("Heat card added to every outdoor session (R-16)")
    return []


def check_r18(ctx) -> list[str]:
    out = []
    for t in ctx.texts:
        for m in VOLUME.finditer(t or ""):
            window = t[max(0, m.start() - 40): m.end() + 40]
            if DRINK.search(window):
                out.append(f"R-18 failed: fixed drinking amount '{m.group(0)}' in: {t[:80]}")
    return out


def check_r21(ctx) -> list[str]:
    banned = engine.EXCLUDED_FOODS[ctx.runner["diet"]]
    out = []
    for t in ctx.texts:
        low = (t or "").lower()
        for food in banned:
            if re.search(r"\b" + re.escape(food), low):
                out.append(f"R-21 failed: '{food}' is not on the {ctx.runner['diet'].replace('_', ' ')} fuel list")
    return out


def check_r28(log: dict) -> list[str]:
    """Logging: 'done' or 'partly' needs effort and minutes. Runs on the log form."""
    if log.get("status") in ("done", "partial"):
        missing = [k for k in ("effort", "minutes") if log.get(k) in (None, "")]
        if missing:
            return [f"R-28: please add {' and '.join(missing)}"]
        if not 1 <= int(log["effort"]) <= 10:
            return ["R-28: effort must be 1 to 10"]
    return []


def check_r30(ctx) -> list[str]:
    return [f"R-30 failed: acute:chronic ratio mentioned in: {t[:80]}" for t in ctx.texts if ACWR.search(t or "")]


def check_r32(ctx) -> list[str]:
    state = engine.screening_state(ctx.conn, ctx.runner["id"])
    if state == "ok" or ctx.seed:
        return []
    return ["R-32: health questions not answered" if state == "unanswered"
            else "R-32: plan locked until a doctor's clearance date is entered"]


def check_r35(ctx) -> list[str]:
    known = rules.ids()
    out = []
    for c in ctx.changes:
        ids = c.get("rule_ids") or []
        if not ids:
            out.append(f"R-35 failed on {c['day']}: no rule cited")
        bad = [i for i in ids if i not in known]
        if bad:
            out.append(f"R-35 failed on {c['day']}: unknown rule {', '.join(bad)}")
    return out


def check_r36(ctx) -> list[str]:
    if ctx.override:
        return ["R-36: a request cannot switch off the safety checks"]
    return []


CHECKS = {  # rule id -> check over a draft
    "R-03": check_r03, "R-05": check_r05, "R-07": check_r07, "R-12": check_r12,
    "R-16": check_r16, "R-18": check_r18, "R-21": check_r21, "R-30": check_r30,
    "R-32": check_r32, "R-35": check_r35, "R-36": check_r36,
}
LOG_CHECKS = {"R-28": check_r28}  # rule id -> check over a log entry
NAMES = {"shape": "Shape", "R-03": "Single-run cap", "R-05": "Weekly cap", "R-07": "Pain stop",
         "R-12": "Final 3 days", "R-16": "Heat card", "R-18": "No fixed drinking amounts",
         "R-21": "Diet", "R-28": "Logging", "R-30": "No ACWR", "R-32": "Health gate",
         "R-35": "Citations", "R-36": "No override"}


def normalise(change: dict) -> dict:
    c = dict(change)
    c["day"] = str(c["day"])
    c["distance_km"] = float(c.get("distance_km") or 0)
    if c["session_type"] in ("rest", "strength"):
        c["distance_km"] = 0.0
    for k in ("zone", "location", "run_walk"):
        if c.get(k) == "none" and k != "location":
            c[k] = None
    return c


def build_ctx(conn, rid, changes, *, today=None, texts=None, runner_request=False,
              override=False, seed=False) -> Ctx:
    today = today or clock.today()
    r = engine.runner(conn, rid)
    original = engine.plan(conn, rid)
    merged = {d: dict(v) for d, v in original.items()}
    changes = [normalise(c) for c in changes]
    for c in changes:
        merged[c["day"]] = {**merged.get(c["day"], {}), **c, "runner_id": rid}
    logs = engine.logs_by_day(conn, rid)
    texts = list(texts or [])
    texts += [c.get("reason") or "" for c in changes]
    for c in changes:
        if c.get("steps"):
            texts += list(c["steps"])
    runs = engine.run_km_by_day(merged, logs, today, engine.extra_km_by_day(conn, rid))
    for d, p in merged.items():  # the race itself is the goal, not training load
        if p.get("session_type") == "race":
            runs.pop(d, None)
    return Ctx(conn=conn, runner=r, today=today, changes=changes, plan=merged, original=original,
               logs=logs, avail=engine.availability(conn, rid), runs=runs, texts=texts,
               runner_request=runner_request, override=override, seed=seed)


def validate(conn, rid, changes, **kw) -> dict:
    ctx = build_ctx(conn, rid, changes, **kw)
    results = [{"rule": "shape", "name": NAMES["shape"], "failures": check_shape(ctx)}]
    for rid_, fn in CHECKS.items():
        results.append({"rule": rid_, "name": NAMES[rid_], "failures": fn(ctx)})
    for r in results:
        r["ok"] = not r["failures"]
    return {"ok": all(r["ok"] for r in results), "checks": results,
            "elevated": ctx.elevated, "notes": ctx.notes,
            "failures": [f for r in results for f in r["failures"]]}


def apply(conn, rid: int, changes: list[dict], *, proposal_id=None, source="model",
          oks: set[str] | None = None, seed=False, today=None) -> dict:
    """Re-validate and write. Elevated-risk days need the runner's OK (R-04)."""
    result = validate(conn, rid, changes, seed=seed, today=today)
    if not result["ok"]:
        return result
    oks = oks or set()
    need = set(result["elevated"]) - oks
    if need and not seed:
        result["ok"] = False
        result["needs_ok"] = sorted(need)
        return result
    now = clock.utc_iso()
    conn.execute("BEGIN")
    try:
        for c in [normalise(x) for x in changes]:
            before = row(conn, "SELECT * FROM plan_day WHERE runner_id=? AND day=?", rid, c["day"])
            doc = {"title": c.get("title") or engine.TYPE_LABEL[c["session_type"]],
                   "steps": c.get("steps") or [], "reason": c.get("reason"),
                   "breaks": c.get("breaks")}
            if c.get("fuel_rehearsal"):
                doc["fuel_rehearsal"] = True
            elevated = c["day"] in result["elevated"]
            vals = (rid, c["day"], c["session_type"], c["distance_km"], c.get("zone"),
                    c.get("location") or "none", c.get("run_walk"), json.dumps(doc),
                    json.dumps(c.get("rule_ids") or []), int(elevated),
                    now if elevated and not seed else None, source)
            conn.execute("""INSERT INTO plan_day (runner_id, day, session_type, distance_km, zone,
                location, run_walk, steps_json, rule_ids, elevated_risk, runner_ok_at, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(runner_id, day) DO UPDATE SET session_type=excluded.session_type,
                distance_km=excluded.distance_km, zone=excluded.zone, location=excluded.location,
                run_walk=excluded.run_walk, steps_json=excluded.steps_json, rule_ids=excluded.rule_ids,
                elevated_risk=excluded.elevated_risk, runner_ok_at=excluded.runner_ok_at,
                source=excluded.source""", vals)
            after = row(conn, "SELECT * FROM plan_day WHERE runner_id=? AND day=?", rid, c["day"])
            conn.execute("""INSERT INTO plan_history (runner_id, day, proposal_id, before_json,
                after_json, changed_at) VALUES (?,?,?,?,?,?)""",
                         (rid, c["day"], proposal_id, plan_row_json(before), plan_row_json(after), now))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    result["applied"] = True
    return result
