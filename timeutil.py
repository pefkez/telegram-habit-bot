import re
from datetime import datetime, timedelta, timezone

TZ_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def parse_tz(tz: str) -> int:
    m = TZ_PATTERN.match((tz or "").strip())
    if not m:
        return 3 * 60
    hours, minutes = int(m.group(2)), int(m.group(3))
    sign = -1 if m.group(1) == "-" else 1
    return sign * (hours * 60 + minutes)


def now_in_tz(tz: str) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=parse_tz(tz))


def today_key(tz: str) -> str:
    return now_in_tz(tz).strftime("%Y-%m-%d")


def hm_now(tz: str) -> str:
    return now_in_tz(tz).strftime("%H:%M")


def add_days_key(key: str, days: int) -> str:
    d = datetime.strptime(key, "%Y-%m-%d") + timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def last_n_days(n: int, tz: str) -> list[str]:
    keys: list[str] = []
    key = today_key(tz)
    for _ in range(n):
        keys.append(key)
        key = add_days_key(key, -1)
    return keys