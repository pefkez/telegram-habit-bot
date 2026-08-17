#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import socket
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from timeutil import hm_now, today_key

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0) or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_PIN = os.getenv("TELEGRAM_API_IP", "")
if API_PIN:
    _real_getaddrinfo = socket.getaddrinfo

    def _pinned_getaddrinfo(host, *args, **kwargs):
        h = host.decode() if isinstance(host, bytes) else host
        if h == "api.telegram.org":
            host = API_PIN
        return _real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _pinned_getaddrinfo

EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")


def habit_title(h) -> str:
    emoji = "" if h["emoji"] == "✅" else h["emoji"]
    return f"{emoji} {h['name']}".strip()

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def greet_text_and_kb(user_id: int, today: str, tz: str):
    habits = db.list_habits(user_id)
    rows = []
    if not habits:
        text = "👋 Привет!\n\n😴 Привычек пока нет.\nДобавь первую: ⚙️ Настройки → ➕ Добавить привычку"
        rows.append([InlineKeyboardButton("⚙️ Настройки", callback_data="greet:settings")])
        return text, InlineKeyboardMarkup(rows)
    streaks = {r["habit"]["id"]: r["current_streak"] for r in db.user_stats(user_id, tz)["rows"]}
    d = datetime.strptime(today, "%Y-%m-%d")
    lines = ["👋 Привет!", "", f"📆 {WEEKDAYS[d.weekday()]} · {d.day} {MONTHS_GEN[d.month]}", ""]
    left = 0
    for h in habits:
        status = db.status_for_date(h["id"], today)
        mark = {"done": "🟢", "skip": "⏭", "none": "🔴"}[status]
        streak = streaks.get(h["id"], 0)
        fire = f" · 🔥 {streak}" if streak > 1 and status != "none" else ""
        lines.append(f"{mark} {habit_title(h)}{fire}")
        if status == "none":
            left += 1
        rows.append([InlineKeyboardButton(f"{mark} {habit_title(h)}", callback_data=f"greet:{h['id']}")])
    lines.append("")
    lines.append("Отметь выполненные привычки ниже 👇" if left else "🎉 Всё отмечено!")
    rows.append(
        [
            InlineKeyboardButton("📊 Статистика", callback_data="greet:stats"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="greet:settings"),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(rows)


MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def month_calendar(habit_id: int, tz: str, today: str) -> str:
    d = datetime.strptime(today, "%Y-%m-%d")
    year, month = d.year, d.month
    next_month = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    days_in_month = (next_month - timedelta(days=1)).day
    cells = []
    for day in range(1, days_in_month + 1):
        key = f"{year}-{month:02d}-{day:02d}"
        if key > today:
            mark = "🔘"
        else:
            mark = {"done": "🟢", "skip": "⏭", "none": "🔴"}[db.status_for_date(habit_id, key)]
        cells.append(f"{day:>2}{mark}")
    lines = []
    for i in range(0, len(cells), 7):
        lines.append(" ".join(cells[i : i + 7]))
    return "\n".join(lines)


def stats_text(user) -> str:
    stats = db.user_stats(user["id"], user["timezone"])
    rows = stats["rows"]
    if not rows:
        return "😴 Добавь привычку — появится статистика: ⚙️ Настройки → ➕ Добавить"
    today = stats["today"]
    d = datetime.strptime(today, "%Y-%m-%d")
    lines = [f"📊 Статистика · {MONTHS[d.month]} {d.year}", ""]
    for r in rows:
        h = r["habit"]
        lines.append(
            f"{habit_title(h)}\n"
            f"🔥 Стрик: {r['current_streak']} дн. · 🏆 Рекорд: {r['best_streak']} дн.\n"
            f"✅ Отметок: {r['done30']}/30"
        )
        lines.append("")
        lines.append(month_calendar(h["id"], user["timezone"], today))
        lines.append("")
    lines.append("🟢 выполнено · 🔴 не выполнено · 🔘 ещё не наступил")
    return "\n".join(lines)


def settings_kb(user) -> InlineKeyboardMarkup:
    cur = user["remind_time"] or "выкл"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить привычку", callback_data="set:add")],
            [InlineKeyboardButton("🗑 Удалить привычку", callback_data="set:del")],
            [InlineKeyboardButton(f"⏰ Напоминание: {cur}", callback_data="set:remind")],
            [InlineKeyboardButton("🔙 В меню", callback_data="set:back")],
        ]
    )

pending: dict[int, str] = {}
pending_msg: dict[int, int] = {}

STATS_BACK = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="stats:back")]])


async def delete_pair(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, prompt_id: int | None, msg_id: int) -> None:
    for mid in (prompt_id, msg_id):
        if mid:
            try:
                await ctx.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass


async def send_greeting(bot, user, today: str | None = None):
    today = today or today_key(user["timezone"])
    text, kb = greet_text_and_kb(user["id"], today, user["timezone"])
    prev = db.get_last_greeting(user["telegram_id"])
    if prev and prev["last_greeting_date"] == today and prev["last_greeting_msg_id"]:
        try:
            await bot.delete_message(chat_id=user["telegram_id"], message_id=prev["last_greeting_msg_id"])
        except Exception:
            pass
    msg = await bot.send_message(chat_id=user["telegram_id"], text=text, reply_markup=kb)
    db.set_last_greeting(user["telegram_id"], today, msg.message_id)
    return msg

HELP = (
    "🔥 <b>Habit Tracker Bot</b>\n\n"
    "Бот пишет тебе сам — жми на привычки и отмечай.\n\n"
    "⚙️ Настройки — добавить/удалить привычку, время напоминания\n"
    "📊 Статистика — стрики и прогресс\n\n"
    "/remind 20:00 — задать время\n/remind off — выключить"
)


def arg_text(text: str, command: str) -> str:
    return text[len(command):].strip()


def parse_emoji_and_name(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw:
        return "", ""
    first = raw.split()[0]
    if len(first) <= 4 and EMOJI_RE.search(first):
        name = raw[len(first):].strip()
        return first, name
    return "", raw


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    await send_greeting(ctx.bot, user)


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
    await update.message.reply_text(f"✅ Добавлено: {habit_title(habit)}")


async def cmd_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    habits = db.list_habits(user["id"])
    if not habits:
        await update.message.reply_text("😴 Пока нет привычек. Добавь: /add 🏃 Бег")
        return
    today = today_key(user["timezone"])
    lines = []
    for i, h in enumerate(habits, 1):
        status = db.status_for_date(h["id"], today)
        mark = {"done": "🟢", "skip": "⏭", "none": "🔴"}[status]
        lines.append(f"{i}. {mark} {habit_title(h)}")
    await update.message.reply_text(f"📋 Привычки на сегодня ({today}):\n\n" + "\n".join(lines))


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
            await update.message.reply_text(f"🎉 Отмечено всё ({marked})!")
        else:
            await update.message.reply_text("👍 На сегодня уже всё отмечено")
        return

    habit = db.find_habit(user["id"], arg)
    if not habit:
        await update.message.reply_text("🤔 Не нашёл такую привычку. Посмотри /habits")
        return
    if not db.checkin(user["id"], habit["id"], today):
        await update.message.reply_text(f"{habit_title(habit)} уже отмечена сегодня")
        return
    streak = next(
        (r["current_streak"] for r in db.user_stats(user["id"], user["timezone"])["rows"] if r["habit"]["id"] == habit["id"]),
        1,
    )
    msg = f"✅ {habit_title(habit)} отмечена"
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
        await update.message.reply_text("🤔 Не нашёл такую привычку. Посмотри /habits")
        return
    today = today_key(user["timezone"])
    if db.status_for_date(habit["id"], today) == "skip":
        await update.message.reply_text(f"⏭ {habit_title(habit)} уже пропущена сегодня")
        return
    db.skip(user["id"], habit["id"], today)
    await update.message.reply_text(f"⏭ {habit_title(habit)} — пропущена, стрик цел")


async def cmd_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/del")
    if not arg:
        await update.message.reply_text("Формат: /del 1 или /del Бег")
        return
    habit = db.find_habit(user["id"], arg)
    if not habit:
        await update.message.reply_text("🤔 Не нашёл такую привычку. Посмотри /habits")
        return
    db.delete_habit(user["id"], habit["id"])
    await update.message.reply_text(f"🗑 Удалено: {habit_title(habit)}")


async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    arg = arg_text(update.message.text, "/remind").strip().lower()
    if arg == "off":
        db.set_remind_time(u.id, None)
        await update.message.reply_text("🔕 Напоминания выключены")
        return
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", arg):
        await update.message.reply_text("Формат: /remind 20:00 (или /remind off)")
        return
    hm = arg if ":" in arg else f"{arg}:00"
    db.set_remind_time(u.id, hm)
    await update.message.reply_text(f"⏰ Напомню в {hm} ({user['timezone']}), если не отметишься")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    await update.message.reply_text(stats_text(user), reply_markup=STATS_BACK)


def habits_kb(user_id: int, action: str, today: str) -> InlineKeyboardMarkup:
    rows = []
    for h in db.list_habits(user_id):
        if action == "done" and db.status_for_date(h["id"], today) == "done":
            continue
        if action == "skip" and db.status_for_date(h["id"], today) == "skip":
            continue
        rows.append([InlineKeyboardButton(habit_title(h), callback_data=f"{action}:{h['id']}")])
    return InlineKeyboardMarkup(rows)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)
    text = update.message.text.strip()

    state = pending.get(u.id)
    if state == "awaiting_add":
        prompt_id = pending_msg.pop(u.id, None)
        emoji, name = parse_emoji_and_name(text)
        if not name:
            await update.message.reply_text("✏️ Напиши название, например 🏃 Бег")
            await delete_pair(ctx, u.id, prompt_id, update.message.message_id)
            return
        del pending[u.id]
        db.add_habit(user["id"], name, emoji)
        await delete_pair(ctx, u.id, prompt_id, update.message.message_id)
        await send_greeting(ctx.bot, user)
        return

    if state == "awaiting_remind":
        prompt_id = pending_msg.pop(u.id, None)
        if text.lower() == "off":
            del pending[u.id]
            db.set_remind_time(u.id, None)
            await update.message.reply_text("🔕 Напоминания выключены", reply_markup=settings_kb(user))
            await delete_pair(ctx, u.id, prompt_id, update.message.message_id)
            return
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", text):
            await update.message.reply_text("Формат: 20:00 (или off)")
            await delete_pair(ctx, u.id, prompt_id, update.message.message_id)
            return
        del pending[u.id]
        db.set_remind_time(u.id, text)
        await update.message.reply_text(f"⏰ Напомню в {text}", reply_markup=settings_kb(user))
        await delete_pair(ctx, u.id, prompt_id, update.message.message_id)
        return

    today = today_key(user["timezone"])

    if text == "📊 Статистика":
        await update.message.reply_text(stats_text(user), reply_markup=STATS_BACK)
    elif text == "⚙️ Настройки":
        await update.message.reply_text("⚙️ Настройки", reply_markup=settings_kb(user))


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    user = db.get_or_create_user(u.id, u.username, u.first_name)

    if q.data == "stats:back":
        await q.answer()
        await q.message.delete()
        return

    if q.data.startswith("set:"):
        action = q.data.split(":", 1)[1]
        if action == "add":
            pending[u.id] = "awaiting_add"
            pending_msg[u.id] = q.message.message_id
            await q.edit_message_text("✏️ Напиши название привычки, например 🏃 Бег")
        elif action == "del":
            kb = habits_kb(user["id"], "del", today_key(user["timezone"]))
            if kb.to_dict()["inline_keyboard"]:
                await q.edit_message_text("🗑 Что удалить?", reply_markup=kb)
            else:
                await q.edit_message_text("😌 Удалять нечего", reply_markup=settings_kb(user))
        elif action == "remind":
            pending[u.id] = "awaiting_remind"
            pending_msg[u.id] = q.message.message_id
            await q.edit_message_text("⏰ Во сколько напомнить? Формат: 20:00 (или off)")
        elif action == "back":
            await q.message.delete()
        return

    if q.data.startswith("greet:"):
        today = today_key(user["timezone"])
        action = q.data.split(":", 1)[1]
        if action == "skipday":
            for h in db.list_habits(user["id"]):
                if db.status_for_date(h["id"], today) == "none":
                    db.skip(user["id"], h["id"], today)
            await q.answer("⏭ День пропущен")
        elif action == "stats":
            await q.answer()
            await q.message.reply_text(stats_text(user), reply_markup=STATS_BACK)
            return
        elif action == "settings":
            await q.answer()
            await q.message.reply_text("⚙️ Настройки", reply_markup=settings_kb(user))
            return
        else:
            habit = next((h for h in db.list_habits(user["id"]) if h["id"] == int(action)), None)
            if not habit:
                await q.answer("🤔 Такой привычки уже нет")
                return
            if not db.checkin(user["id"], habit["id"], today):
                await q.answer("👍 Уже отмечена")
            else:
                streak = next(
                    (r["current_streak"] for r in db.user_stats(user["id"], user["timezone"])["rows"]
                     if r["habit"]["id"] == habit["id"]),
                    1,
                )
                await q.answer(f"✅ Отмечено! 🔥 {streak} дн." if streak > 1 else "✅ Отмечено!")
        text, kb = greet_text_and_kb(user["id"], today, user["timezone"])
        await q.edit_message_text(text, reply_markup=kb)
        return

    action, habit_id = q.data.split(":")
    habit = next((h for h in db.list_habits(user["id"]) if h["id"] == int(habit_id)), None)
    if not habit:
        await q.edit_message_text("🤔 Такой привычки уже нет")
        return

    today = today_key(user["timezone"])
    if action == "done":
        if not db.checkin(user["id"], habit["id"], today):
            await q.edit_message_text(f"{habit_title(habit)} уже отмечена сегодня")
            return
        streak = next(
            (r["current_streak"] for r in db.user_stats(user["id"], user["timezone"])["rows"] if r["habit"]["id"] == habit["id"]),
            1,
        )
        msg = f"✅ {habit_title(habit)} отмечена"
        if streak > 1:
            msg += f" · 🔥 {streak} дн. подряд"
        await q.edit_message_text(msg)
    elif action == "skip":
        if db.status_for_date(habit["id"], today) == "skip":
            await q.edit_message_text(f"⏭ {habit_title(habit)} уже пропущена")
            return
        db.skip(user["id"], habit["id"], today)
        await q.edit_message_text(f"⏭ {habit_title(habit)} — пропущена, стрик цел")
        await send_greeting(ctx.bot, user)
    elif action == "del":
        db.delete_habit(user["id"], habit["id"])
        await q.message.delete()
        await send_greeting(ctx.bot, user)


async def cmd_adminstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u.id != ADMIN_ID:
        await update.message.reply_text("🚫 Нет доступа")
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
        active = "✅" if x["id"] in s["active_today"] else ("🟣" if x["id"] in s["active_week"] else "🔘")
        lines.append(
            f"{active} {tag} — привычек: {s['habit_count'].get(x['id'], 0)}, "
            f"отметок: {s['checkin_count'].get(x['id'], 0)}, последняя: {last}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def reminder_loop(app: Application) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            for u in db.users_for_reminder():
                if hm_now(u["timezone"]) != u["remind_time"]:
                    continue
                today = today_key(u["timezone"])
                if u["last_reminded"] == today:
                    continue
                await send_greeting(app.bot, u, today)
                db.set_last_reminded(u["telegram_id"], today)
                log.info("reminder sent to %s", u["telegram_id"])
        except Exception:
            log.exception("reminder loop error")


async def post_init(app: Application) -> None:
    asyncio.create_task(reminder_loop(app))


def main() -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан. Скопируй .env.example в .env и вставь токен от @BotFather")
        return
    if not ADMIN_ID:
        log.warning("ADMIN_ID не задан — команда /adminstats будет недоступна (узнай свой id у @userinfobot)")

    while True:
        try:
            db.init_db()
            app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

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
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
            app.add_handler(CallbackQueryHandler(on_callback))

            log.info("бот запущен")
            app.run_polling()
        except KeyboardInterrupt:
            log.info("остановлен")
            break
        except Exception:
            log.exception("бот упал, перезапуск через 10 сек")
            time.sleep(10)


if __name__ == "__main__":
    main()