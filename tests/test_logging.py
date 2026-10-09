"""Logging after a run: time, pace, breaks, extra runs, and what they change."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import clock, engine, main, validator
from tests.conftest import MOM, PRATHOSH, change


@pytest.fixture
def client(conn):
    return TestClient(main.app)


def at(y, m, d, hour_sgt=10):
    clock.freeze(datetime(y, m, d, hour_sgt - 8, 0, tzinfo=timezone.utc))


def test_days_before_setup_are_not_imported(conn):
    plan = engine.plan(conn, MOM)
    assert "2026-10-06" not in plan and "2026-10-08" not in plan
    assert engine.is_run(plan["2026-10-10"])


def test_unlogged_past_runs_count_as_nothing(conn):
    at(2026, 10, 14)
    runs = engine.run_km_by_day(engine.plan(conn, MOM), engine.logs_by_day(conn, MOM), clock.today())
    assert "2026-10-13" not in runs  # planned, not logged
    assert runs["2026-10-15"] == 3  # still to come


def test_pace_is_calculated(client):
    at(2026, 10, 13)
    r = client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3, "minutes": 31 + 20 / 60,
                                      "effort": 4, "breaks": "planned"}).json()
    assert "10:27 /km" in r["message"] and "31:20" in r["message"]


def test_extra_run_counts_towards_totals(client, conn, offline):
    at(2026, 10, 12)
    r = client.post("/api/log", json={"rid": PRATHOSH, "day": "2026-10-11", "status": "done", "distance_km": 4,
                                      "minutes": 30, "effort": 4, "extra": True})
    assert r.json()["ok"] and "extra" in offline[-1][1]
    assert engine.extra_km_by_day(conn, PRATHOSH) == {"2026-10-11": 4.0}
    weeks = {w["week"]: w["km"] for w in engine.weekly_km(conn, PRATHOSH, clock.today(), weeks=1)}
    assert weeks["2026-10-05"] >= 4
    days = client.get(f"/api/plan?rid={PRATHOSH}").json()["days"]
    assert next(d for d in days if d["day"] == "2026-10-11")["extra_km"] == 4


def test_extra_run_never_marks_planned_session_done(client, conn):
    at(2026, 10, 13)
    client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 2, "minutes": 20,
                                  "effort": 3, "extra": True})
    assert not client.get(f"/api/today?rid={MOM}").json()["logged_today"]


def test_extra_run_tightens_the_checks(client, conn):
    """A big extra week raises the base, but a run far over the longest is still capped by R-03."""
    at(2026, 10, 12)
    client.post("/api/log", json={"rid": PRATHOSH, "day": "2026-10-11", "status": "done", "distance_km": 4,
                                  "minutes": 30, "effort": 4, "extra": True})
    res = validator.validate(conn, PRATHOSH, [change("2026-10-13", km=12)])
    assert not res["ok"]


def test_extra_run_needs_distance(client):
    r = client.post("/api/log", json={"rid": MOM, "status": "done", "minutes": 20, "effort": 3, "extra": True})
    assert r.status_code == 422


def test_short_run_triggers_redraft(client, offline):
    at(2026, 10, 13)
    client.post("/api/log", json={"rid": MOM, "status": "partial", "distance_km": 1.5, "minutes": 18, "effort": 5})
    assert "1.5 of 3 km" in offline[-1][1]


def test_many_breaks_trigger_redraft(client, offline):
    at(2026, 10, 13)
    client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3, "minutes": 38, "effort": 5,
                                  "breaks": "many"})
    assert "breaks" in offline[-1][1]


def test_extra_breaks_block_runwalk_step(client, conn):
    at(2026, 10, 10)
    client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 2.5, "minutes": 27, "effort": 4,
                                  "breaks": "some"})
    at(2026, 10, 13)
    client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3, "minutes": 31, "effort": 4,
                                  "breaks": "planned"})
    assert not engine.runwalk_progress(conn, engine.runner(conn, MOM), clock.today())["unlocked"]


def test_planned_time_trial_is_saved(client, conn):
    tt = next((d for d, p in engine.plan(conn, PRATHOSH).items() if p["session_type"] == "time_trial"), None)
    if not tt:
        pytest.skip("no planned time trial in the seed")
    y, m, d = map(int, tt.split("-"))
    at(y, m, d)
    r = client.post("/api/log", json={"rid": PRATHOSH, "status": "done", "distance_km": 5, "minutes": 30,
                                      "effort": 9}).json()
    assert "time trial" in r["message"]
    assert engine.latest_trial(conn, PRATHOSH)["time_s"] == 1800
