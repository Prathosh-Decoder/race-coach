"""Tap count -> action: 5 on/off, 6 next flavour, 7 Big Day. Any other count does nothing."""
from datetime import timedelta

from .. import clock, config
from . import bigday, quiet, state


def knock(conn, rid: int, taps: int, today=None) -> str | None:
    today = today or clock.today()
    s = state.get(conn, rid)
    if taps == 5:
        if s["enabled"]:
            state.update(conn, rid, enabled=0, big_day_km=None, big_day_until=None)
            return "off"
        state.update(conn, rid, enabled=1)
        return "on"
    if taps == 6 and s["enabled"]:
        i = state.FLAVOURS.index(s["flavour"])
        state.update(conn, rid, flavour=state.FLAVOURS[(i + 1) % len(state.FLAVOURS)])
        return "flavour"
    if taps == 7 and s["enabled"]:
        if state.big_day_active(s):
            state.update(conn, rid, big_day_km=None, big_day_until=None)
            return "disarm"
        if bigday.allowed(conn, rid, today, quiet.reasons(conn, rid, today)):
            until = (clock.now_utc() + timedelta(hours=config.BIG_DAY_HOURS)).isoformat(timespec="seconds")
            state.update(conn, rid, big_day_km=bigday.draw_km(), big_day_until=until)
            return "arm"
    return None
