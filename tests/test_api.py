import json

import pytest
from fastapi.testclient import TestClient

from app import coach, engine, main
from tests.conftest import MOM, PRATHOSH


@pytest.fixture
def client(conn):
    return TestClient(main.app)


def test_health_gate(client, conn):
    conn.execute("DELETE FROM screening WHERE runner_id=?", (MOM,))
    assert client.get(f"/api/today?rid={MOM}").json()["card"] is None
    assert client.get(f"/api/plan?rid={MOM}").status_code == 403
    r = client.post(f"/api/runners/{MOM}/screening",
                    json={"active_3x_week": False, "known_disease": False, "symptoms": True}).json()
    assert r["gate"] == "locked"
    assert client.get(f"/api/today?rid={MOM}").json()["card"] is None
    r = client.post(f"/api/runners/{MOM}/clearance", json={"doctor_cleared_on": "2026-10-09"}).json()
    assert r["gate"] == "ok"


def test_log_requires_effort_and_minutes(client):
    r = client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3})
    assert r.status_code == 422
    r = client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3, "minutes": 30, "effort": 4})
    assert r.json()["ok"]


def test_missed_session_triggers_redraft(client, offline):
    from datetime import datetime, timezone
    from app import clock
    clock.freeze(datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc))
    client.post("/api/log", json={"rid": MOM, "day": "2026-10-10", "status": "missed"})
    assert offline and "missed" in offline[-1][1]


def test_hard_run_triggers_redraft(client, offline, conn):
    client.post("/api/log", json={"rid": PRATHOSH, "day": "2026-10-09", "status": "done",
                                  "distance_km": 3, "minutes": 25, "effort": 9})
    assert "harder" in offline[-1][1]


def test_pain_flow(client, conn):
    client.post("/api/pain", json={"rid": MOM, "pain": "sharp", "pain_where": "knee"})
    assert engine.open_pain(conn, MOM)
    client.post("/api/pain/gone", json={"rid": MOM})
    assert not engine.open_pain(conn, MOM)


def test_ok_elevated_run(client, conn):
    assert client.post("/api/plan/ok", json={"rid": MOM, "day": "2026-10-13"}).json()["ok"]
    assert engine.plan(conn, MOM)["2026-10-13"]["runner_ok_at"]
    assert client.post("/api/plan/ok", json={"rid": MOM, "day": "2026-10-12"}).status_code == 400


def test_cannot_log_future(client):
    r = client.post("/api/log", json={"rid": MOM, "day": "2026-10-20", "status": "missed"})
    assert r.status_code == 400


def test_ics_export(client):
    r = client.get(f"/api/export.ics?rid={PRATHOSH}")
    assert r.text.startswith("BEGIN:VCALENDAR") and "Long" in r.text


def test_fallback_when_offline(conn, monkeypatch):
    monkeypatch.setattr(coach, "online", lambda: False)
    out = coach.draft(conn, PRATHOSH, "missed session")
    assert out["status"] in ("fallback", "awaiting_runner")


def test_model_draft_retry_then_fallback(conn, monkeypatch):
    """A draft that fails twice never reaches the plan."""
    monkeypatch.setattr(coach, "online", lambda: True)
    bad = {"summary": "More!", "changes": [{"day": "2026-10-13", "session_type": "long", "distance_km": 20,
           "zone": "long", "location": "outdoor", "run_walk": "none", "rule_ids": ["R-04"], "reason": "go long"}]}
    calls = []
    monkeypatch.setattr(coach, "chat", lambda *a, **k: calls.append(1) or json.dumps(bad))
    before = engine.plan(conn, PRATHOSH)
    out = coach.draft(conn, PRATHOSH, "test")
    assert len(calls) == 2
    assert out["status"] in ("fallback", "awaiting_runner") and out.get("fallback", True)
    assert engine.plan(conn, PRATHOSH) == before


def test_cascade_shrinking_a_week_can_break_the_next(conn):
    """R-05 compares with the real previous week, so a smaller week lowers next week's cap."""
    from app import validator
    res = validator.validate(conn, PRATHOSH, [{"day": "2026-10-14", "session_type": "easy", "distance_km": 3,
                                               "location": "either", "rule_ids": ["R-02"], "reason": "x"}])
    assert any("2026-10-19" in f for f in res["failures"])


def test_model_draft_ok_then_accept(conn, monkeypatch):
    monkeypatch.setattr(coach, "online", lambda: True)
    good = {"summary": "Easier day after a hard run.", "changes": [{"day": "2026-10-14", "session_type": "easy",
            "distance_km": 5, "zone": "easy", "location": "either", "run_walk": "none", "rule_ids": ["R-02"],
            "reason": "Swap intervals for an easy run."}]}
    monkeypatch.setattr(coach, "chat", lambda *a, **k: json.dumps(good))
    out = coach.draft(conn, PRATHOSH, "felt hard")
    assert out["status"] == "awaiting_runner"
    assert engine.plan(conn, PRATHOSH)["2026-10-14"]["session_type"] == "intervals"  # not yet
    assert coach.decide(conn, PRATHOSH, out["proposal_id"], True)["status"] == "accepted"
    assert engine.plan(conn, PRATHOSH)["2026-10-14"]["session_type"] == "easy"
    hist = conn.execute("SELECT * FROM plan_history WHERE proposal_id=?", (out["proposal_id"],)).fetchall()
    assert len(hist) == 1


def test_tell_coach_offline_reader(client, conn):
    r = client.post("/api/tell", json={"rid": MOM, "text": "Felt very hard today"}).json()
    assert r["record"]["kind"] == "felt_hard"
    r = client.post("/api/tell", json={"rid": MOM, "text": "Is chest pain normal?"}).json()
    assert "doctor" in r["reply"]


def test_screens_served(client):
    html = client.get("/").text
    assert "app.js" in html and "countdown" in html
    assert client.get("/static/css/app.css").status_code == 200
