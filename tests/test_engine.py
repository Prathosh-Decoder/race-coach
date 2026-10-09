import os
import time
from datetime import date, datetime, timezone

import pytest

from app import clock, engine, seed
from tests.conftest import MOM, PRATHOSH


@pytest.mark.parametrize("laptop_tz", ["UTC", "Asia/Tokyo", "America/Los_Angeles"])
@pytest.mark.parametrize("utc, sgt_day", [
    (datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc), date(2026, 10, 13)),  # 00:30 SGT
    (datetime(2026, 10, 13, 15, 30, tzinfo=timezone.utc), date(2026, 10, 13)),  # 23:30 SGT
])
def test_today_is_singapore_date(monkeypatch, laptop_tz, utc, sgt_day):
    monkeypatch.setenv("TZ", laptop_tz)
    time.tzset()
    try:
        clock.freeze(utc)
        assert clock.today() == sgt_day
    finally:
        clock.freeze(None)
        monkeypatch.delenv("TZ")
        time.tzset()
        os.environ.pop("TZ", None)


def test_weeks_start_on_monday():
    assert clock.week_start(date(2026, 10, 18)) == date(2026, 10, 12)  # Sunday
    assert clock.week_start(date(2026, 10, 12)) == date(2026, 10, 12)  # Monday


def test_riegel_worked_numbers(conn):
    p = engine.prediction(conn, engine.runner(conn, PRATHOSH))
    assert p["formula"] == "2:27:12"
    assert (p["low"], p["high"]) == ("2:36", "2:48")


def test_runwalk_worked_numbers(conn):
    p = engine.prediction(conn, engine.runner(conn, MOM))
    assert (p["low"], p["high"]) == ("3:17", "3:27")


def test_treadmill_speed():
    assert engine.fmt_speed(8.0) == "7.5"


def test_paces_from_trial_not_goal(conn):
    before = engine.paces(conn, engine.runner(conn, PRATHOSH))["zones"]["easy"]["pace"]
    conn.execute("UPDATE runner SET goal_time_s=7200 WHERE id=?", (PRATHOSH,))
    assert engine.paces(conn, engine.runner(conn, PRATHOSH))["zones"]["easy"]["pace"] == before


def test_seeds_import_cleanly(data_dir, frozen):
    from app import db
    c = db.connect()
    db.migrate(c)
    for f in seed.SEEDS:
        assert seed.import_seed(c, seed.load(f))["ok"]


def test_baseline_log_is_not_a_hard_run(conn):
    assert engine.hard_hold(conn, PRATHOSH, clock.today()) is None


def test_pain_blocks_runs(conn):
    clock.freeze(datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc))
    conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, logged_at)
                    VALUES (?, '2026-10-12', 'missed', 'sharp', 'x')""", (MOM,))
    v = engine.today_view(conn, MOM)
    assert v["card"]["blocked"] == "pain" and v["card"]["distance_km"] == 0


def test_streak_counts_rest_days_but_not_extra_running(conn):
    clock.freeze(datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc))
    assert engine.streak(conn, engine.runner(conn, MOM), clock.today()) >= 1
