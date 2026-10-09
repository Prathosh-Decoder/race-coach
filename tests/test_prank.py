import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import clock, engine, main, prank
from app.prank import pick, state
from tests.conftest import MOM

BANNED_WORDS = re.compile(r"prank|joke|kidding", re.I)
BANNED_TOPICS = re.compile(r"\b(weight|body|fat|thin|skinny|looks|pretty|age|old|food|diet|eat|calories?)\b", re.I)


@pytest.fixture
def client(conn):
    return TestClient(main.app)


def run_day(conn):
    """Move the clock to Mom's next run day (Tue 13 Oct)."""
    clock.freeze(datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc))
    return clock.today()


def test_off_by_default_and_taps(client, conn):
    assert client.get(f"/api/today?rid={MOM}").json()["extras"] is None
    client.post("/api/knock", json={"rid": MOM, "taps": 4})
    assert not state.get(conn, MOM)["enabled"]
    client.post("/api/knock", json={"rid": MOM, "taps": 6})  # only while on
    assert state.get(conn, MOM)["flavour"] == "goose"
    client.post("/api/knock", json={"rid": MOM, "taps": 5})
    assert state.get(conn, MOM)["enabled"]
    client.post("/api/knock", json={"rid": MOM, "taps": 6})
    assert state.get(conn, MOM)["flavour"] == "royal"
    client.post("/api/knock", json={"rid": MOM, "taps": 5})
    assert not state.get(conn, MOM)["enabled"]


def test_big_day_is_display_only(client, conn):
    run_day(conn)
    before = engine.plan(conn, MOM)
    state.update(conn, MOM, enabled=1)
    client.post("/api/knock", json={"rid": MOM, "taps": 7})
    s = state.get(conn, MOM)
    assert state.big_day_active(s) and 15 <= s["big_day_km"] <= 23
    today = client.get(f"/api/today?rid={MOM}").json()
    assert today["extras"]["big_day"]["km"] == s["big_day_km"]
    assert today["card"]["distance_km"] == 3  # the real session is still underneath
    client.get(f"/api/plan?rid={MOM}")
    ics = client.get(f"/api/export.ics?rid={MOM}").text
    event = ics.split("DTSTART;VALUE=DATE:20261013")[1].split("END:VEVENT")[0]
    assert "3 km" in event and f"{s['big_day_km']:g} km" not in event
    client.post("/api/log", json={"rid": MOM, "status": "done", "distance_km": 3.1, "minutes": 33, "effort": 4})
    assert engine.plan(conn, MOM) == before
    assert engine.logs_by_day(conn, MOM)["2026-10-13"]["distance_km"] == 3.1


def test_big_day_switches_off_after_3_hours(client, conn):
    run_day(conn)
    state.update(conn, MOM, enabled=1)
    client.post("/api/knock", json={"rid": MOM, "taps": 7})
    clock.freeze(clock.now_utc() + timedelta(hours=3, minutes=1))
    assert client.get(f"/api/today?rid={MOM}").json()["extras"]["big_day"] is None


def test_big_day_never_on_rest_days_or_pain(client, conn):
    state.update(conn, MOM, enabled=1)  # Fri 9 Oct is a rest day for Mom
    client.post("/api/knock", json={"rid": MOM, "taps": 7})
    assert not state.big_day_active(state.get(conn, MOM))
    run_day(conn)
    conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, logged_at)
                    VALUES (?, '2026-10-12', 'missed', 'sharp', 'x')""", (MOM,))
    client.post("/api/knock", json={"rid": MOM, "taps": 7})
    assert not state.big_day_active(state.get(conn, MOM))


def test_silent_moments(conn):
    state.update(conn, MOM, enabled=1)
    conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, logged_at)
                    VALUES (?, '2026-10-09', 'missed', 'sharp', 'x')""", (MOM,))
    ex = prank.extras_for(conn, MOM)
    assert not any(ex[k] for k in ("nickname", "greeting", "saved", "side_quest", "spin", "big_day"))


def test_env_switch_silences(conn, monkeypatch):
    state.update(conn, MOM, enabled=1)
    monkeypatch.setenv("RACE_COACH_PRANK", "off")
    assert prank.extras_for(conn, MOM) is None


def test_never_says_it(client, conn):
    run_day(conn)
    for flav in state.FLAVOURS:
        state.update(conn, MOM, enabled=1, flavour=flav)
        assert not BANNED_WORDS.search(client.get(f"/api/today?rid={MOM}").text)
    for path in ("index.html", "js/app.js", "css/app.css"):
        text = (main.STATIC / path).read_text()
        visible = re.sub(r"(Extras|extras|Knock|knock)", "", text)
        assert not BANNED_WORDS.search(visible), path


def test_lines_file():
    lines = pick.lines()
    for flav, slots in lines.items():
        for slot, items in slots.items():
            for line in items if isinstance(items, list) else [items]:
                if not isinstance(line, str):
                    continue
                assert len(line) <= 80, line
                assert not BANNED_TOPICS.search(line), (flav, line)
                assert not BANNED_WORDS.search(line), line
            if slot == "saved":
                assert all(re.search("Logged|Saved|Recorded", x) for x in items), flav


def test_same_line_all_day_new_line_next_day():
    from datetime import date
    a = pick.line(MOM, "goose", "greeting", date(2026, 10, 9))
    assert a == pick.line(MOM, "goose", "greeting", date(2026, 10, 9))
    days = {pick.line(MOM, "goose", "greeting", date(2026, 10, d)) for d in range(9, 13)}
    assert len(days) == 4


def test_prank_names(conn):
    from tests.conftest import PRATHOSH
    assert prank.extras_for(conn, MOM) is None  # off: the real name shows
    for rid, name in ((MOM, "Mum"), (PRATHOSH, "Prat")):
        state.update(conn, rid, enabled=1, flavour="son")  # "son" has no nickname of its own
        assert prank.extras_for(conn, rid)["nickname"] == name
    conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, logged_at)
                    VALUES (?, '2026-10-09', 'missed', 'sharp', 'x')""", (MOM,))
    assert prank.extras_for(conn, MOM)["nickname"] is None  # silent during a pain lock
