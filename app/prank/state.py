"""Reads and writes prank_state (one row per profile)."""
from .. import clock
from ..db import row

FLAVOURS = ["goose", "royal", "commentator", "trailer", "son", "surprise"]


def get(conn, rid: int) -> dict:
    s = row(conn, "SELECT * FROM prank_state WHERE runner_id=?", rid)
    if not s:
        conn.execute("INSERT INTO prank_state (runner_id, changed_at) VALUES (?,?)", (rid, clock.utc_iso()))
        s = row(conn, "SELECT * FROM prank_state WHERE runner_id=?", rid)
    return s


def update(conn, rid: int, **kw):
    get(conn, rid)
    kw["changed_at"] = clock.utc_iso()
    sets = ", ".join(f"{k}=?" for k in kw)
    conn.execute(f"UPDATE prank_state SET {sets} WHERE runner_id=?", (*kw.values(), rid))


def big_day_active(s: dict) -> bool:
    return bool(s.get("big_day_km") and s.get("big_day_until") and s["big_day_until"] > clock.utc_iso())
