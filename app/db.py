"""Connection, migrations and daily backup. The database lives outside the Git folder."""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import clock, config


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn, backup_first: bool = True):
    files = sorted(config.MIGRATIONS_DIR.glob("*.sql"))
    have = current_version(conn)
    todo = [(int(re.match(r"(\d+)", f.name).group(1)), f) for f in files]
    todo = [(v, f) for v, f in todo if v > have]
    if not todo:
        return
    if backup_first and have > 0:
        backup(conn, tag="pre-migration")
    for version, f in todo:
        sql = f.read_text()
        conn.execute("BEGIN")
        try:
            for stmt in [s for s in sql.split(";\n") if s.strip()]:
                if stmt.strip().upper().startswith("PRAGMA"):
                    continue
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def backup(conn, tag: str = "daily", dest: Path | None = None) -> Path:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = clock.now().strftime("%Y-%m-%d_%H%M%S")
    dest = dest or config.BACKUP_DIR / f"coach_{stamp}_{tag}.db"
    out = sqlite3.connect(dest)
    with out:
        conn.backup(out)
    out.close()
    olds = sorted(config.BACKUP_DIR.glob("coach_*.db"))
    for old in olds[:-config.BACKUPS_KEPT]:
        old.unlink()
    return dest


def daily_backup(conn):
    today = clock.today().isoformat()
    if config.BACKUP_DIR.exists() and any(config.BACKUP_DIR.glob(f"coach_{today}_*")):
        return None
    return backup(conn)


def row(conn, sql, *args):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def rows(conn, sql, *args):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def plan_row_json(r: dict | None) -> str | None:
    if r is None:
        return None
    keep = ("day", "session_type", "distance_km", "zone", "location", "run_walk",
            "steps_json", "rule_ids", "elevated_risk", "source")
    return json.dumps({k: r.get(k) for k in keep})
