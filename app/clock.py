"""Singapore date and time helpers. Never use date.today(): it follows the laptop's zone."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

_frozen: datetime | None = None  # tests can freeze time with freeze()


def freeze(dt: datetime | None):
    global _frozen
    _frozen = dt


def now_utc() -> datetime:
    return _frozen.astimezone(timezone.utc) if _frozen else datetime.now(timezone.utc)


def now() -> datetime:
    return now_utc().astimezone(SGT)


def today() -> date:
    return now().date()


def utc_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def to_sgt(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc).astimezone(SGT)


def week_start(d: date) -> date:
    """Monday of the Singapore week containing d."""
    return d - timedelta(days=d.weekday())


def parse(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def days_between(a, b) -> int:
    return (parse(b) - parse(a)).days


def daterange(a, b):
    a, b = parse(a), parse(b)
    while a <= b:
        yield a
        a += timedelta(days=1)
