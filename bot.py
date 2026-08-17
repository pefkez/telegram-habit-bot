#!/usr/bin/env python3
import asyncio
import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import db
from timeutil import hm_now, today_key

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0) or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

HELP = (
    "🔥 <b>Habit Tracker Bot</b>\n\n"
    "Привычки:\n"
    "/add 🏃 Бег — добавить привычку (эмодзи необязателен)\n"
    "/habits — список привычек на сегодня\n"
    "/done 1 — отметить сегодня (номер или название, /done all — всё)\n"
    "/skip 1 — пропустить день без разрыва стрика\n"
    "/del 1 — удалить привычку\n\n"
    "Прогресс:\n"
    "/stats — стрики и статистика\n"
    "/remind 20:00 — напоминание в это время (выключить: /remind off)\n\n"
    "/help — эта справка"
)


def arg_text(text: str, command: str) -> str:
    return text[len(command):].strip()


def parse_emoji_and_name(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw:
        return "✅", ""
    first = raw.split()[0]
    if len(first) <= 4 and EMOJI_RE.search(first):
        name = raw[len(first):].strip()
        return first, name
    return "✅", raw


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.get_or_create_user(u.id, u.username, u.first_name)
    await update.message.reply_text(
        f"Привет, {u.first_name or 'друг'}! Отмечай привычки прямо в Telegram.\n\n{HELP}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode="HTML")


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    emoji, name = parse_emoji_and_name(arg_text(update.message.text, "/add"))
    if not name:
        await update.message.reply_text("Формат: /add 🏃 Бег (или /add Бег)")
        return
    habit = db.add_habit(user["id"], name, emoji)
    await update.message.reply_text(f"Добавлено: {habit['emoji']} {habit['name']}")


async def cmd_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    habits = db.list_habits(user["id"])
    if not habits:
        await update.message.reply_text("Пока нет привычек. Добавь: /add 🏃 Бег")
        return
    today = today_key(user["timezone"])
    lines = []
    for i, h in enumerate(habits, 1):
        status = db.status_for_date(h["id"], today)
        mark = {"done": "✅", "skip": "⏭", "none": "⬜"}[status]
        lines.append(f"{i}. {mark} {h['emoji']} {h['name']}")
    await update.message.reply_text(f"Привычки на сегодня ({today}):\n\n" + "\n".join(lines))


async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/done").lower()
    today = today_key(user["timezone"])

    if not arg:
        await update.message.reply_text("Формат: /done 1, /done Бег или /done all")
        return

    if arg == "all":
        habits = db.list_habits(user["id"])
        marked = 0
        for h in habits:
            if db.status_for_date(h["id"], today) == "none":
                db.checkin(user["id"], h["id"], today)
                marked += 1
        if marked:
            await update.message.reply_text(f"Отмечено всё ({marked}) 🎉")
        else:
            await update.message.reply_text("На сегодня уже всё отмечено")
        return

    habit = db.find_habit(user["id"], arg)
    if not habit:
        await update.message.reply_text("Не нашёл такую привычку. Посмотри /habits")
        return
    if not db.checkin(user["id"], habit["id"], today):
        await update.message.reply_text(f"{habit['emoji']} {habit['name']} уже отмечена сегодня")
        return
    streak = next(
        (r["current_streak"] for r in db.user_stats(user["id"], user["timezone"])["rows"] if r["habit"]["id"] == habit["id"]),
        1,
    )
    msg = f"✅ {habit['emoji']} {habit['name']} отмечена"
    if streak > 1:
        msg += f" · 🔥 {streak} дн. подряд"
    await update.message.reply_text(msg)


async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/skip")
    if not arg:
        await update.message.reply_text("Формат: /skip 1 или /skip Бег")
        return
    habit = db.find_habit(user["id"], arg)
    if not habit:
        await update.message.reply_text("Не нашёл такую привычку. Посмотри /habits")
        return
    today = today_key(user["timezone"])
    if db.status_for_date(habit["id"], today) == "skip":
        await update.message.reply_text(f"{habit['emoji']} {habit['name']} уже пропущена сегодня")
        return
    db.skip(user["id"], habit["id"], today)
    await update.message.reply_text(f"⏭ {habit['emoji']} {habit['name']} — пропущена, стрик не сломан")


async def cmd_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/del")
    if not arg:
        await update.message.reply_text("Формат: /del 1 или /del Бег")
        return
    habit = db.find_habit(user["id"], arg)
    if not habit:
        await update.message.reply_text("Не нашёл такую привычку. Посмотри /habits")
        return
    db.delete_habit(user["id"], habit["id"])
    await update.message.reply_text(f"Удалено: {habit['emoji']} {habit['name']}")


async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/remind").strip().lower()
    if arg == "off":
        db.set_remind_time(u.id, None)
        await update.message.reply_text("Напоминания выключены")
        return
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", arg):
        await update.message.reply_text("Формат: /remind 20:00 (или /remind off)")
        return
    hm = arg if ":" in arg else f"{arg}:00"
    db.set_remind_time(u.id, hm)
    await update.message.reply_text(f"Напомню в {hm} ({user['timezone']}), если не отметишься")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    stats = db.user_stats(user["id"], user["timezone"])
    rows = stats["rows"]
    if not rows:
        await update.message.reply_text("Добавь привычку — появится статистика: /add 🏃 Бег")
        return

    week_done = sum(r["done7"] for r in rows)
    week_total = 7 * len(rows)
    week_pct = round(week_done / week_total * 100) if week_total else 0
    done_today = sum(1 for r in rows if r["today"] == "done")

    lines = [
        f"📊 Статистика ({stats['today']})",
        "",
        f"Сегодня: {done_today}/{len(rows)} · неделя: {week_pct}%",
        "",
    ]
    for r in rows:
        h = r["habit"]
        today_mark = {"done": "✅", "skip": "⏭", "none": "⬜"}[r["today"]]
        lines.append(
            f"{today_mark} {h['emoji']} {h['name']}\n"
            f"   🔥 стрик: {r['current_streak']} дн. · 🏆 лучший: {r['best_streak']} · "
            f"📅 за 30 дн.: {r['done30']}/30 · ⏭ пропусков: {r['skips30']}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_adminstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа")
        return
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    tz = user["timezone"]
    s = db.admin_stats(tz)

    today = today_key(tz)
    new_today = sum(1 for x in s["users"] if x["created_at"] == today)

    days_line = " · ".join(f"{d['date'][5:]} ({d['count']})" for d in s["daily"])

    lines = [
        "👥 <b>Пользователи бота</b>",
        "",
        f"Всего: <b>{len(s['users'])}</b> · новых сегодня: {new_today}, за 7 дн.: {s['new_week']}, за 30 дн.: {s['new_month']}",
        f"Активных сегодня: <b>{len(s['active_today'])}</b> · за 7 дней: {len(s['active_week'])}",
        f"Привычек: <b>{len(s['habits'])}</b> · отметок: {len(s['checkins'])} · пропусков: {len(s['skips'])}",
        "",
        f"Отметки за 7 дней: {days_line}",
        "",
        "<b>Пользователи:</b>",
    ]

    for x in s["users"]:
        name = x["username"] or x["first_name"] or f"id{x['telegram_id']}"
        tag = f"@{name}" if x["username"] else name
        last = s["last_checkin"].get(x["id"], "—")
        active = "✅" if x["id"] in s["active_today"] else ("🟣" if x["id"] in s["active_week"] else "⬜")
        lines.append(
            f"{active} {tag} — привычек: {s['habit_count'].get(x['id'], 0)}, "
            f"отметок: {s['checkin_count'].get(x['id'], 0)}, последняя: {last}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def reminder_loop(app: Application) -> None:
    """Каждую минуту проверяет, кому пора напоминать."""
    while True:
        await asyncio.sleep(60)
        try:
            for u in db.users_for_reminder():
                if hm_now(u["timezone"]) != u["remind_time"]:
                    continue
                today = today_key(u["timezone"])
                if u["last_reminded"] == today:
                    continue
                habits = db.list_habits(u["id"])
                missed = [h for h in habits if db.status_for_date(h["id"], today) == "none"]
                if not missed:
                    continue
                lines = "\n".join(f"{h['emoji']} {h['name']}" for h in missed)
                await app.bot.send_message(
                    chat_id=u["telegram_id"],
                    text=f"⏰ Напоминание! Не отмечено {len(missed)}:\n\n{lines}\n\nОтметь: /done all или по одному",
                )
                db.set_last_reminded(u["telegram_id"], today)
                log.info("reminder sent to %s", u["telegram_id"])
        except Exception:
            log.exception("reminder loop error")


def main() -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан. Скопируй .env.example в .env и вставь токен от @BotFather")
        return
    if not ADMIN_ID:
        log.warning("ADMIN_ID не задан — команда /adminstats будет недоступна (узнай свой id у @userinfobot)")

    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("habits", cmd_habits))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("del", cmd_del))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("adminstats", cmd_adminstats))

    app.job_queue.run_once(
        lambda ctx: asyncio.create_task(reminder_loop(app)),
        when=1,
    )

    log.info("бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()