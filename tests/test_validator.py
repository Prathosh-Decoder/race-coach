"""Every MUST check with at least one passing and one failing case."""
import pytest

from app import engine, rules, validator
from tests.conftest import MOM, PRATHOSH, change


def failing(res, rule):
    return next(c for c in res["checks"] if c["rule"] == rule)["failures"]


def test_every_must_rule_has_a_check_and_back():
    checks = set(validator.CHECKS) | set(validator.LOG_CHECKS)
    assert checks == rules.must_ids()


def test_shape_pass(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "shape") == []


@pytest.mark.parametrize("day", ["2026-10-08", "2026-10-09", "2026-12-05"])
def test_shape_rejects_past_today_and_race_day(conn, day):
    assert failing(validator.validate(conn, PRATHOSH, [change(day)]), "shape")


def test_shape_rejects_two_on_one_day_and_cant_run_days(conn):
    res = validator.validate(conn, PRATHOSH, [change("2026-10-13"), change("2026-10-13")])
    assert any("twice" in f for f in failing(res, "shape"))
    res = validator.validate(conn, MOM, [change("2026-11-03", run_walk="1:1")])  # Japan, no running
    assert any("can't run" in f for f in failing(res, "shape"))


def test_shape_rejects_treadmill_without_one(conn):
    res = validator.validate(conn, MOM, [change("2026-10-13", location="treadmill", run_walk="1:1")])
    assert any("treadmill" in f for f in failing(res, "shape"))


def test_r03_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13", km=5)]), "R-03") == []
    res = validator.validate(conn, PRATHOSH, [change("2026-10-13", km=12)])
    assert any("more than double" in f for f in failing(res, "R-03"))


def test_r04_sets_elevated_flag(conn):
    res = validator.validate(conn, PRATHOSH, [change("2026-10-13", km=6), change("2026-10-16", "rest", 0)])
    assert res["ok"], res["failures"]
    assert "2026-10-13" in res["elevated"]
    assert "2026-10-13" not in validator.validate(conn, PRATHOSH, [change("2026-10-13", km=5)])["elevated"]


def test_r05_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-20", km=4)]), "R-05") == []
    res = validator.validate(conn, PRATHOSH, [change("2026-10-20", km=9), change("2026-10-24", km=8)])
    assert any("R-05" in f for f in failing(res, "R-05"))


def test_r07_pass_and_fail(conn):
    assert failing(validator.validate(conn, MOM, [change("2026-10-13", run_walk="1:1")]), "R-07") == []
    conn.execute("""INSERT INTO session_log (runner_id, day, status, pain, logged_at)
                    VALUES (?, '2026-10-09', 'missed', 'sharp', 'x')""", (MOM,))
    res = validator.validate(conn, MOM, [change("2026-10-13", run_walk="1:1")])
    assert failing(res, "R-07")


def test_r12_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-12-03", km=3)]), "R-12") == []
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-12-03", "tempo", 4)]), "R-12")
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-12-04", km=6)]), "R-12")


def test_r16_pass_and_fail(conn, monkeypatch):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-16") == []
    monkeypatch.setattr(engine, "HEAT_CARD", None)
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-16")


def test_r18_pass_and_fail(conn):
    ok = validator.validate(conn, PRATHOSH, [change("2026-10-13", reason="Drink when thirsty.")])
    assert failing(ok, "R-18") == []
    bad = validator.validate(conn, PRATHOSH, [change("2026-10-13", reason="Drink 500 ml of water before.")])
    assert failing(bad, "R-18")


def test_r21_pass_and_fail(conn):
    ok = validator.validate(conn, PRATHOSH, [change("2026-10-13", reason="Rehearse with dates.")])
    assert failing(ok, "R-21") == []
    bad = validator.validate(conn, PRATHOSH, [change("2026-10-13", reason="Try a gel at 40 minutes.")])
    assert failing(bad, "R-21")


def test_r28_pass_and_fail():
    assert validator.check_r28({"status": "done", "effort": 4, "minutes": 30}) == []
    assert validator.check_r28({"status": "missed"}) == []
    assert validator.check_r28({"status": "partial", "effort": None, "minutes": 20})
    assert validator.check_r28({"status": "done", "effort": 11, "minutes": 20})


def test_r28_in_database(conn):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO session_log (runner_id, day, status, logged_at) VALUES (1, '2026-10-09', 'done', 'x')")


def test_r30_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-30") == []
    bad = validator.validate(conn, PRATHOSH, [change("2026-10-13")], texts=["Your ACWR is 1.4"])
    assert failing(bad, "R-30")
    bad = validator.validate(conn, PRATHOSH, [change("2026-10-13")], texts=["acute:chronic load is high"])
    assert failing(bad, "R-30")


def test_r30_no_ratio_in_code():
    from pathlib import Path
    for f in Path("app").rglob("*.py"):
        text = f.read_text().lower()
        assert "def acwr" not in text and "acute_chronic" not in text, f


def test_r32_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-32") == []
    conn.execute("UPDATE screening SET symptoms=1 WHERE runner_id=?", (PRATHOSH,))
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-32")
    conn.execute("UPDATE screening SET doctor_cleared_on='2026-10-09' WHERE runner_id=?", (PRATHOSH,))
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-32") == []


def test_r35_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")]), "R-35") == []
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13", rule_ids=["R-99"])]), "R-35")
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13", rule_ids=[])]), "R-35")


def test_r36_pass_and_fail(conn):
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")], runner_request=True), "R-36") == []
    assert failing(validator.validate(conn, PRATHOSH, [change("2026-10-13")], override=True), "R-36")


def test_runner_request_for_21km_before_race(conn):
    """Five days out it can only go ahead with an explicit OK; three days out it is rejected."""
    res = validator.validate(conn, PRATHOSH, [change("2026-11-30", "long", 21)], runner_request=True)
    assert not res["ok"] or "2026-11-30" in res["elevated"]
    res = validator.validate(conn, PRATHOSH, [change("2026-12-02", "long", 21)], runner_request=True)
    assert not res["ok"]


def test_apply_needs_ok_for_elevated(conn):
    draft = [change("2026-10-13", km=6), change("2026-10-16", "rest", 0)]
    res = validator.apply(conn, PRATHOSH, draft)
    assert res["needs_ok"] == ["2026-10-13"]
    assert engine.plan(conn, PRATHOSH)["2026-10-13"]["distance_km"] == 3
    res = validator.apply(conn, PRATHOSH, draft, oks={"2026-10-13"})
    assert res["applied"]
    assert engine.plan(conn, PRATHOSH)["2026-10-13"]["distance_km"] == 6


def test_failed_draft_never_written(conn):
    before = engine.plan(conn, PRATHOSH)
    res = validator.apply(conn, PRATHOSH, [change("2026-10-13", km=15)], oks={"2026-10-13"})
    assert not res["ok"]
    assert engine.plan(conn, PRATHOSH) == before
