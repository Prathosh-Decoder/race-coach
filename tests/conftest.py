from datetime import datetime, timezone

import pytest

from app import clock, coach, config, db, seed


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "coach.db")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.delenv("RACE_COACH_PRANK", raising=False)
    return tmp_path


@pytest.fixture
def frozen():
    """Fri 9 Oct 2026, 10:00 am Singapore time."""
    clock.freeze(datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc))
    yield
    clock.freeze(None)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(coach, "online", lambda: False)
    drafts = []
    monkeypatch.setattr(coach, "start_draft", lambda conn, rid, trigger, **kw: drafts.append((rid, trigger)))
    return drafts


@pytest.fixture
def conn(data_dir, frozen, offline):
    c = db.connect()
    db.migrate(c)
    for f in seed.SEEDS:
        res = seed.import_seed(c, seed.load(f))
        assert res["ok"], res
    for rid in (1, 2):
        c.execute("""INSERT INTO screening (runner_id, answered_on, active_3x_week, known_disease, symptoms)
                     VALUES (?, '2026-10-09', 1, 0, 0)""", (rid,))
    yield c
    c.close()


PRATHOSH, MOM = 1, 2


def change(day, session_type="easy", km=3.0, **kw):
    return {"day": day, "session_type": session_type, "distance_km": km, "zone": None,
            "location": kw.pop("location", "outdoor"), "run_walk": kw.pop("run_walk", None),
            "rule_ids": kw.pop("rule_ids", ["R-03"]), "reason": kw.pop("reason", "test"), **kw}
