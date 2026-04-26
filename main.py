# -*- coding: utf-8 -*-
import asyncio
import sqlite3
import sys
import logging
import os
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# === НАСТРОЙКА ЛОГИРОВАНИЯ ДЛЯ RENDER ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("8604443712:AAGPC5TWB7QU_cJD-tKVAgw5zjnRMoAasQ8")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment variables")
    sys.exit(1)

DB_NAME = "assistant.db"
ADMIN_ID = None   # можно указать числовой ID, если нужно

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        premium_until DATE,
        timezone INTEGER DEFAULT 3,
        report_hour INTEGER DEFAULT 22
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        is_done BOOLEAN DEFAULT 0,
        priority TEXT DEFAULT 'medium',
        due_date TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        remind_at TIMESTAMP,
        repeats_left INTEGER DEFAULT 1,
        repeat_interval INTEGER,
        is_active BOOLEAN DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS habits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        last_tracked DATE,
        streak INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def is_premium(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row or row[0] is None:
        conn.close()
        return False
    premium_until = datetime.strptime(row[0], "%Y-%m-%d").date()
    conn.close()
    return premium_until >= date.today()

def get_tasks_count(user_id: int, active_only: bool = True) -> int:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if active_only:
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_done=0", (user_id,))
    else:
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_reminders_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM reminders WHERE user_id=? AND is_active=1 AND repeats_left>0", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_habits_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM habits WHERE user_id=?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def add_task(user_id: int, title: str, due_date: str = None, priority: str = "medium"):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (user_id, title, due_date, priority) VALUES (?, ?, ?, ?)",
              (user_id, title, due_date, priority))
    conn.commit()
    conn.close()

def complete_task(task_id: int, user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET is_done=1 WHERE id=? AND user_id=?", (task_id, user_id))
    conn.commit()
    conn.close()

def get_user_tasks(user_id: int, only_active=True):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if only_active:
        c.execute("SELECT id, title, priority, due_date FROM tasks WHERE user_id=? AND is_done=0 ORDER BY priority DESC, due_date", (user_id,))
    else:
        c.execute("SELECT id, title, is_done, priority, due_date FROM tasks WHERE user_id=? ORDER BY due_date", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "priority": r[2], "due_date": r[3]} for r in rows]

def add_reminder(user_id: int, text: str, remind_at: datetime, repeats: int = 1):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, text, remind_at, repeats_left) VALUES (?, ?, ?, ?)",
              (user_id, text, remind_at, repeats))
    conn.commit()
    conn.close()

def get_due_reminders(now: datetime):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, user_id, text, remind_at, repeats_left FROM reminders WHERE is_active=1 AND repeats_left>0 AND remind_at <= ?", (now,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "user_id": r[1], "text": r[2], "remind_at": r[3], "repeats_left": r[4]} for r in rows]

def decrement_reminder(reminder_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE reminders SET repeats_left = repeats_left - 1 WHERE id=?", (reminder_id,))
    c.execute("UPDATE reminders SET is_active=0 WHERE id=? AND repeats_left<=0", (reminder_id,))
    conn.commit()
    conn.close()

def add_habit(user_id: int, name: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO habits (user_id, name, last_tracked, streak) VALUES (?, ?, NULL, 0)", (user_id, name))
    conn.commit()
    conn.close()

def track_habit(user_id: int, habit_name: str) -> str:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today_str = date.today().isoformat()
    c.execute("SELECT id, last_tracked, streak FROM habits WHERE user_id=? AND name=?", (user_id, habit_name))
    row = c.fetchone()
    if not row:
        conn.close()
        return "❌ Привычка не найдена. Сначала добавьте её через /add_habit"
    hid, last, streak = row
    if last == today_str:
        conn.close()
        return "✅ Сегодня вы уже отмечали эту привычку!"
    if last == (date.today() - timedelta(days=1)).isoformat():
        streak += 1
    else:
        streak = 1
    c.execute("UPDATE habits SET last_tracked=?, streak=? WHERE id=?", (today_str, streak, hid))
    conn.commit()
    conn.close()
    return f"🔥 Отлично! Серия: {streak} день(дней)."

def get_habits(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT name, last_tracked, streak FROM habits WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"name": r[0], "last_tracked": r[1], "streak": r[2]} for r in rows]

def set_premium(user_id: int, days: int = 30):
    until = (date.today() + timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO users (user_id, premium_until) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET premium_until=excluded.premium_until",
              (user_id, until))
    conn.commit()
    conn.close()

def ensure_user(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

# === ОБРАБОТЧИКИ КОМАНД ===
ADD_TASK_WAITING = 1
ADD_REMINDER_TEXT, ADD_REMINDER_DATETIME, ADD_REMINDER_REPEATS = range(10, 13)
ADD_HABIT_NAME = 20

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)
    text = (
        "🤖 *Личный помощник* — трекер задач, привычек и эффективности.\n\n"
        "🔹 *Бесплатно:* до 5 задач, до 2 напоминаний, до 2 привычек, ежедневный отчёт.\n"
        "🔹 *Premium (99 руб/мес):* безлимит, приоритеты, расширенная статистика, Pomodoro.\n\n"
        "📌 *Команды:*\n"
        "/add_task – добавить задачи (несколько, каждая с новой строки)\n"
        "/tasks – список задач с кнопками «Сделано»\n"
        "/add_reminder – установить напоминание\n"
        "/reminders – мои напоминания\n"
        "/add_habit – добавить привычку\n"
        "/habits – список привычек\n"
        "/track_habit <название> – отметить выполнение привычки\n"
        "/daily – отчёт за сегодня\n"
        "/premium – купить подписку (тестовая активация)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Пришлите список задач, каждую с новой строки.\nПример:\nКупить молоко\nПозвонить маме\nЗакончить отчёт")
    return ADD_TASK_WAITING

async def add_task_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = update.message.text.strip().split("\n")
    tasks = [line.strip() for line in lines if line.strip()]
    if not tasks:
        await update.message.reply_text("❌ Пустой список.")
        return ConversationHandler.END

    premium = is_premium(user_id)
    max_tasks = 999 if premium else 5
    current_tasks = get_tasks_count(user_id, active_only=True)

    if current_tasks + len(tasks) > max_tasks:
        limit_msg = "безлимит" if premium else f"{max_tasks}"
        await update.message.reply_text(f"❌ Лимит активных задач ({limit_msg}). Выполните старые через /tasks или купите Premium (/premium).")
        return ConversationHandler.END

    for title in tasks:
        add_task(user_id, title)
    await update.message.reply_text(f"✅ Добавлено {len(tasks)} задач. Всего активных: {current_tasks + len(tasks)}.")
    await show_tasks(update, context)
    return ConversationHandler.END

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tasks = get_user_tasks(user_id, only_active=True)
    if not tasks:
        await update.message.reply_text("🎉 У вас нет активных задач!")
        return
    text = "📋 *Ваши задачи*\n\n"
    keyboard = []
    for t in tasks:
        emoji = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(t["priority"], "⚪")
        due = f" (до {t['due_date']})" if t["due_date"] else ""
        text += f"{emoji} {t['title']}{due}\n"
        keyboard.append([InlineKeyboardButton(f"✅ {t['title'][:30]}", callback_data=f"done_{t['id']}")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def task_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        user_id = query.from_user.id
        complete_task(task_id, user_id)
        await query.edit_message_text("✅ Задача выполнена.")

async def add_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏰ Напишите текст напоминания.")
    return ADD_REMINDER_TEXT

async def reminder_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reminder_text"] = update.message.text
    await update.message.reply_text("📅 Укажите дату и время: ГГГГ-ММ-ДД ЧЧ:ММ\nПример: 2026-05-01 15:30")
    return ADD_REMINDER_DATETIME

async def reminder_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        remind_at = datetime.strptime(update.message.text, "%Y-%m-%d %H:%M")
    except:
        await update.message.reply_text("❌ Неверный формат.")
        return ADD_REMINDER_DATETIME
    if remind_at <= datetime.now():
        await update.message.reply_text("❌ Дата должна быть в будущем.")
        return ADD_REMINDER_DATETIME
    context.user_data["remind_at"] = remind_at
    await update.message.reply_text("🔁 Сколько раз напомнить? (число, по умолчанию 1)")
    return ADD_REMINDER_REPEATS

async def reminder_repeats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repeats = 1
    if update.message.text.isdigit():
        repeats = int(update.message.text)
    user_id = update.effective_user.id
    premium = is_premium(user_id)
    max_reminders = 999 if premium else 2
    current = get_reminders_count(user_id)
    if current >= max_reminders:
        await update.message.reply_text(f"❌ Лимит напоминаний ({max_reminders}). Купите Premium.")
        return ConversationHandler.END
    text = context.user_data["reminder_text"]
    remind_at = context.user_data["remind_at"]
    add_reminder(user_id, text, remind_at, repeats)
    await update.message.reply_text(f"✅ Напоминание установлено: «{text}» в {remind_at.strftime('%Y-%m-%d %H:%M')}, повторов: {repeats}")
    return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, text, remind_at, repeats_left FROM reminders WHERE user_id=? AND is_active=1 AND repeats_left>0", (user_id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Нет активных напоминаний.")
        return
    text = "⏰ *Ваши напоминания*\n\n"
    for r in rows:
        rt = datetime.strptime(r[2], "%Y-%m-%d %H:%M:%S.%f")
        text += f"• {r[1]} – {rt.strftime('%d.%m %H:%M')} (осталось: {r[3]})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def add_habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    premium = is_premium(user_id)
    max_habits = 999 if premium else 2
    current = get_habits_count(user_id)
    if current >= max_habits:
        await update.message.reply_text(f"❌ Лимит привычек ({max_habits}). Купите Premium.")
        return ConversationHandler.END
    await update.message.reply_text("🏋️ Введите название привычки (например: «Зарядка», «Чтение 30 мин»)")
    return ADD_HABIT_NAME

async def add_habit_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    user_id = update.effective_user.id
    add_habit(user_id, name)
    await update.message.reply_text(f"✅ Привычка «{name}» добавлена! Отмечайте: /track_habit {name}")
    return ConversationHandler.END

async def list_habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    habits = get_habits(user_id)
    if not habits:
        await update.message.reply_text("📭 Нет привычек. Добавьте /add_habit")
        return
    text = "🏆 *Ваши привычки*\n\n"
    for h in habits:
        last = h["last_tracked"] if h["last_tracked"] else "никогда"
        text += f"• {h['name']} – серия: {h['streak']} дней (посл.: {last})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def track_habit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажите название: /track_habit Зарядка")
        return
    habit_name = " ".join(context.args)
    user_id = update.effective_user.id
    result = track_habit(user_id, habit_name)
    await update.message.reply_text(result)

async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    premium = is_premium(user_id)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today_start = datetime.combine(date.today(), datetime.min.time())
    c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_done=1 AND created_at >= ?", (user_id, today_start))
    done = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND created_at >= ?", (user_id, today_start))
    total = c.fetchone()[0]
    habits = get_habits(user_id)
    today_str = date.today().isoformat()
    tracked = sum(1 for h in habits if h["last_tracked"] == today_str)
    conn.close()

    if not premium:
        text = f"📊 *Итоги дня*\n✅ Задач выполнено: {done} из {total}\n📈 Привычек отмечено: {tracked} из {len(habits)}"
    else:
        percent = (done / total * 100) if total else 0
        bar_len = int(percent // 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        text = f"📈 *Полный отчёт (Premium)*\nЗадач: {done}/{total} ({percent:.1f}%)\n[{bar}] {percent:.0f}%\nПривычки: {tracked}/{len(habits)}\n"
        if total > 0:
            if percent > 70:
                text += "🔥 Супер!"
            elif percent > 40:
                text += "😐 Неплохо."
            else:
                text += "⚠️ Низкая продуктивность."
    await update.message.reply_text(text, parse_mode="Markdown")

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_premium(user_id):
        await update.message.reply_text("Premium уже активен.")
        return
    set_premium(user_id, days=30)
    await update.message.reply_text("🎉 Premium активирован на 30 дней (тест). Теперь безлимит и расширенная статистика.")

async def reminder_loop(app: Application):
    """Фоновая проверка напоминаний каждые 30 секунд"""
    while True:
        try:
            now = datetime.now()
            reminders = get_due_reminders(now)
            for rem in reminders:
                try:
                    await app.bot.send_message(rem["user_id"], f"🔔 Напоминание: {rem['text']}")
                    decrement_reminder(rem["id"])
                except Exception as e:
                    logger.error(f"Ошибка отправки напоминания {rem['id']}: {e}")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Ошибка в reminder_loop: {e}")
            await asyncio.sleep(30)

async def main():
    logger.info("Запуск бота...")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("habits", list_habits))
    app.add_handler(CommandHandler("track_habit", track_habit_command))
    app.add_handler(CommandHandler("daily", daily_report))
    app.add_handler(CommandHandler("premium", premium_command))

    add_task_conv = ConversationHandler(
        entry_points=[CommandHandler("add_task", add_task_start)],
        states={ADD_TASK_WAITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    app.add_handler(add_task_conv)

    add_reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("add_reminder", add_reminder_start)],
        states={
            ADD_REMINDER_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_text)],
            ADD_REMINDER_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_datetime)],
            ADD_REMINDER_REPEATS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_repeats)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    app.add_handler(add_reminder_conv)

    add_habit_conv = ConversationHandler(
        entry_points=[CommandHandler("add_habit", add_habit_start)],
        states={ADD_HABIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_habit_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    app.add_handler(add_habit_conv)

    app.add_handler(CallbackQueryHandler(task_done_callback, pattern="^done_"))

    # Запуск фонового цикла напоминаний
    asyncio.create_task(reminder_loop(app))

    logger.info("Бот запущен и готов к работе")
    # Используем polling без привязки к веб-серверу
    await app.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.exception("Критическая ошибка при запуске")
        sys.exit(1)
