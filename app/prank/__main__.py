"""Terminal controls.

    python -m app.prank status
    python -m app.prank on|off|disarm            [--runner Mom]
    python -m app.prank flavour royal            [--runner Mom]
    python -m app.prank bigday 18                [--runner Mom]
    python -m app.prank sound on|off             [--runner Mom]
"""
import sys
from datetime import timedelta

from .. import clock, config, db
from . import state


def main(argv):
    conn = db.connect()
    db.migrate(conn)
    name = "Mom"
    if "--runner" in argv:
        i = argv.index("--runner")
        name = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "status":
        for r in db.rows(conn, "SELECT id, name FROM runner ORDER BY id"):
            s = state.get(conn, r["id"])
            armed = f", Big Day {s['big_day_km']:g} km until {clock.to_sgt(s['big_day_until']):%H:%M}" \
                if state.big_day_active(s) else ""
            print(f"{r['name']}: {'ON' if s['enabled'] else 'off'}, flavour {s['flavour']}, "
                  f"sound {'on' if s['sound'] else 'off'}{armed}")
        if config.prank_silenced():
            print("RACE_COACH_PRANK=off: everything is silenced (settings kept).")
        return 0
    r = db.row(conn, "SELECT id FROM runner WHERE name=?", name)
    if not r:
        print(f"No profile named {name}")
        return 1
    rid = r["id"]
    if cmd == "on":
        state.update(conn, rid, enabled=1)
    elif cmd == "off":
        state.update(conn, rid, enabled=0, big_day_km=None, big_day_until=None)
    elif cmd == "disarm":
        state.update(conn, rid, big_day_km=None, big_day_until=None)
    elif cmd == "flavour" and len(argv) > 1 and argv[1] in state.FLAVOURS:
        state.update(conn, rid, flavour=argv[1])
    elif cmd == "bigday" and len(argv) > 1:
        until = (clock.now_utc() + timedelta(hours=config.BIG_DAY_HOURS)).isoformat(timespec="seconds")
        state.update(conn, rid, enabled=1, big_day_km=float(argv[1]), big_day_until=until)
    elif cmd == "sound" and len(argv) > 1:
        state.update(conn, rid, sound=1 if argv[1] == "on" else 0)
    else:
        print(__doc__)
        return 1
    return main(["status"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
