import sqlite3
from pathlib import Path

from timeutil import add_days_key, last_n_days, today_key

DB_PATH = Path(__file__).parent / "habits.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id   INTEGER UNIQUE NOT NULL,
  username      TEXT,
  first_name    TEXT,
  timezone      TEXT NOT NULL DEFAULT '+03:00',
  remind_time   TEXT,
  created_at    TEXT NOT NULL,
  last_reminded TEXT
);
CREATE TABLE IF NOT EXISTS habits (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  emoji      TEXT NOT NULL DEFAULT '✅',
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkins (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
  date     TEXT NOT NULL,
  UNIQUE(habit_id, date)
);
CREATE TABLE IF NOT EXISTS skips (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
  date     TEXT NOT NULL,
  UNIQUE(habit_id, date)
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def get_or_create_user(telegram_id: int, username: str | None, first_name: str | None) -> sqlite3.Row:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            return row
        cur = conn.execute(
            "INSERT INTO users (telegram_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, username, first_name, today_key("+00:00")),
        )
        return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def set_remind_time(telegram_id: int, hm: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET remind_time = ? WHERE telegram_id = ?", (hm, telegram_id))


def list_habits(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM habits WHERE user_id = ? AND active = 1 ORDER BY id", (user_id,)
        ).fetchall()


def add_habit(user_id: int, name: str, emoji: str) -> sqlite3.Row:
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO habits (user_id, name, emoji, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, emoji, today_key("+00:00")),
        )
        return conn.execute("SELECT * FROM habits WHERE id = ?", (cur.lastrowid,)).fetchone()


def delete_habit(user_id: int, habit_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id))


def find_habit(user_id: int, text: str) -> sqlite3.Row | None:
    habits = list_habits(user_id)
    text = text.strip().lower()
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(habits):
            return habits[idx]
    for h in habits:
        if h["name"].lower() == text or text in h["name"].lower():
            return h
    return None


def checkin(user_id: int, habit_id: int, date: str) -> bool:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO checkins (habit_id, date) VALUES (?, ?)", (habit_id, date))
        return conn.total_changes > 0


def skip(user_id: int, habit_id: int, date: str) -> bool:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO skips (habit_id, date) VALUES (?, ?)", (habit_id, date))
        return conn.total_changes > 0


def status_for_date(habit_id: int, date: str) -> str:
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM checkins WHERE habit_id = ? AND date = ?", (habit_id, date)).fetchone():
            return "done"
        if conn.execute("SELECT 1 FROM skips WHERE habit_id = ? AND date = ?", (habit_id, date)).fetchone():
            return "skip"
        return "none"


def covered_dates(habit_id: int) -> tuple[set[str], set[str]]:
    with get_conn() as conn:
        done = {r["date"] for r in conn.execute("SELECT date FROM checkins WHERE habit_id = ?", (habit_id,))}
        skips = {r["date"] for r in conn.execute("SELECT date FROM skips WHERE habit_id = ?", (habit_id,))}
        return done, skips


def user_stats(user_id: int, tz: str) -> dict:
    today = today_key(tz)
    last30 = set(last_n_days(30, tz))
    week = set(last_n_days(7, tz))
    habits = list_habits(user_id)

    rows = []
    for h in habits:
        done, skips = covered_dates(h["id"])
        covered = done | skips

        streak = 0
        key = today
        if key not in covered:
            yesterday = add_days_key(key, -1)
            if yesterday not in covered:
                streak = 0
            else:
                key = yesterday
        if key in covered:
            while key in covered:
                streak += 1
                key = add_days_key(key, -1)

        best = 0
        run = 0
        key = add_days_key(today, -369)
        end = today
        guard = 0
        while key <= end and guard < 370:
            if key in covered:
                run += 1
                best = max(best, run)
            else:
                run = 0
            key = add_days_key(key, 1)
            guard += 1

        rows.append({
            "habit": h,
            "current_streak": streak,
            "best_streak": best,
            "done30": sum(1 for d in last30 if d in done),
            "skips30": sum(1 for d in last30 if d in skips),
            "done7": sum(1 for d in week if d in done),
            "today": status_for_date(h["id"], today),
        })
    return {"today": today, "rows": rows}


def admin_stats(tz: str) -> dict:
    today = today_key(tz)
    week = set(last_n_days(7, tz))
    month = set(last_n_days(30, tz))

    with get_conn() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        habits = conn.execute("SELECT * FROM habits").fetchall()
        checkins = conn.execute("SELECT * FROM checkins").fetchall()
        skips = conn.execute("SELECT * FROM skips").fetchall()

    habit_by_id = {h["id"]: h for h in habits}
    user_by_habit = {h["id"]: h["user_id"] for h in habits}

    active_today = set()
    active_week = set()
    checkin_count = {}
    last_checkin = {}
    daily_checkins = {}
    for c in checkins:
        uid = user_by_habit.get(c["habit_id"])
        if uid is None:
            continue
        checkin_count[uid] = checkin_count.get(uid, 0) + 1
        daily_checkins[c["date"]] = daily_checkins.get(c["date"], 0) + 1
        if c["date"] == today:
            active_today.add(uid)
        if c["date"] in week:
            active_week.add(uid)
        if c["date"] > last_checkin.get(uid, ""):
            last_checkin[uid] = c["date"]

    habit_count = {}
    for h in habits:
        habit_count[h["user_id"]] = habit_count.get(h["user_id"], 0) + 1

    new_week = sum(1 for u in users if u["created_at"] in week)
    new_month = sum(1 for u in users if u["created_at"] in month)

    days = last_n_days(7, tz)
    days.reverse()
    daily = [{"date": d, "count": daily_checkins.get(d, 0)} for d in days]

    return {
        "users": users,
        "habits": habits,
        "checkins": checkins,
        "skips": skips,
        "active_today": active_today,
        "active_week": active_week,
        "checkin_count": checkin_count,
        "last_checkin": last_checkin,
        "habit_count": habit_count,
        "new_week": new_week,
        "new_month": new_month,
        "daily": daily,
    }


def users_for_reminder() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE remind_time IS NOT NULL").fetchall()


def set_last_reminded(telegram_id: int, date: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_reminded = ? WHERE telegram_id = ?", (date, telegram_id))