"""
30 дней к чистой речи — Telegram-бот
Детский логопедический центр «Вершина»
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from yookassa import Configuration, Payment

from content import (
    AGE_GROUPS,
    PROBLEMS,
    format_exercise,
    get_exercise,
    get_track,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Конфиг ──────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_KEY     = os.getenv("YOOKASSA_SECRET_KEY", "")
ADMIN_IDS        = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x]
PRICE_RUB        = int(os.getenv("PRICE_RUB", "490"))
CHANNEL_URL      = os.getenv("CHANNEL_URL", "https://t.me/vershinamoskva")
SUPPORT_URL      = os.getenv("SUPPORT_URL", "https://t.me/neurovershinaadmin")
DB_PATH          = "bot.db"
TEST_MODE        = not YOOKASSA_SHOP_ID or YOOKASSA_SHOP_ID == "test"

Configuration.account_id  = YOOKASSA_SHOP_ID
Configuration.secret_key  = YOOKASSA_KEY

bot       = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp        = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ─── FSM ─────────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    age     = State()
    problem = State()
    time    = State()
    pay     = State()

# ─── БД ──────────────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       INTEGER UNIQUE,
                username    TEXT,
                age_group   TEXT,
                problem     TEXT,
                track       TEXT,
                paid        INTEGER DEFAULT 0,
                day_num     INTEGER DEFAULT 0,
                send_hour   INTEGER DEFAULT 9,
                payment_id  TEXT,
                started_at  TEXT,
                paid_at     TEXT
            )
        """)
        await db.commit()

async def upsert_user(tg_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, started_at) VALUES (?,?,?)",
            (tg_id, username, datetime.now().isoformat()),
        )
        await db.commit()

async def set_field(tg_id: int, **kwargs):
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [tg_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {sets} WHERE tg_id=?", vals)
        await db.commit()

async def get_user(tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_all_active() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE (paid=1 OR (paid=0 AND day_num BETWEEN 1 AND 3)) AND day_num < 30"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def count_all() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE paid=1") as c:
            paid = (await c.fetchone())[0]
    return total, paid

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def kb_age() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👶 1.5–3 года",  callback_data="age_young")],
        [InlineKeyboardButton(text="🧒 3–5 лет",     callback_data="age_middle")],
        [InlineKeyboardButton(text="🧒 5–7 лет",     callback_data="age_older")],
    ])

def kb_problem() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Мало говорит / не говорит",     callback_data="prob_launch")],
        [InlineKeyboardButton(text="🔤 Не произносит звук Р",           callback_data="prob_sound_r")],
        [InlineKeyboardButton(text="📚 Хочу развить речь в целом",      callback_data="prob_general")],
    ])

def kb_time() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☀️ 8:00",   callback_data="time_8"),
         InlineKeyboardButton(text="🌤 9:00",   callback_data="time_9")],
        [InlineKeyboardButton(text="🌅 10:00",  callback_data="time_10"),
         InlineKeyboardButton(text="🕛 12:00",  callback_data="time_12")],
        [InlineKeyboardButton(text="🌆 18:00",  callback_data="time_18"),
         InlineKeyboardButton(text="🌙 20:00",  callback_data="time_20")],
    ])

def kb_pay(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {PRICE_RUB} ₽", url=url)],
        [InlineKeyboardButton(text="✅ Я оплатил — проверить", callback_data="check_pay")],
        [InlineKeyboardButton(text="❓ Вопрос / помощь", url=SUPPORT_URL)],
    ])

def kb_support() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать логопеду", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="📢 Наш Telegram-канал", url=CHANNEL_URL)],
    ])

def kb_next_day(day: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сделали! Завтра жду следующее",
                              callback_data=f"done_{day}")],
        [InlineKeyboardButton(text="❓ Вопрос логопеду", url=SUPPORT_URL)],
    ])

# ─── Хендлеры ────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await upsert_user(msg.from_user.id, msg.from_user.username or "")
    user = await get_user(msg.from_user.id)

    if user and user["paid"] and user["day_num"] > 0:
        await msg.answer(
            f"С возвращением! 👋\n\n"
            f"Ты на *дне {user['day_num']} из 30*. "
            f"Следующее задание придёт в {user['send_hour']}:00.\n\n"
            f"Хочешь получить задание прямо сейчас? Напиши /today",
        )
        return

    await state.clear()
    await msg.answer(
        "👋 Привет! Это бот от нейропсихолога и логопеда.\n\n"
        "Здесь вы получите *30 ежедневных заданий* для развития речи ребёнка — "
        "коротких, практичных и проверенных логопедом.\n\n"
        "Для начала скажите: сколько лет вашему ребёнку?",
        reply_markup=kb_age(),
    )
    await state.set_state(Reg.age)

@dp.callback_query(Reg.age, F.data.startswith("age_"))
async def cb_age(call: types.CallbackQuery, state: FSMContext):
    age = call.data.replace("age_", "")
    await state.update_data(age=age)
    await call.message.edit_text(
        f"Отлично! Возраст: *{AGE_GROUPS[age]}*\n\n"
        "Что вас беспокоит больше всего?",
        reply_markup=kb_problem(),
    )
    await state.set_state(Reg.problem)
    await call.answer()

@dp.callback_query(Reg.problem, F.data.startswith("prob_"))
async def cb_problem(call: types.CallbackQuery, state: FSMContext):
    problem = call.data.replace("prob_", "")
    data = await state.get_data()
    age = data["age"]
    track = get_track(age, problem)

    # Если выбрал Р, но ребёнку < 3 лет — мягко переключаем
    note = ""
    if problem == "sound_r" and age == "young":
        note = (
            "\n\n_Звук Р обычно формируется после 5 лет. "
            "Для вашего возраста подберём трек на запуск и развитие речи в целом._"
        )

    await state.update_data(problem=problem, track=track)
    await call.message.edit_text(
        f"Понял! Тема: *{PROBLEMS[problem]}*{note}\n\n"
        "В какое время вам удобно получать ежедневное задание?",
        reply_markup=kb_time(),
    )
    await state.set_state(Reg.time)
    await call.answer()

@dp.callback_query(Reg.time, F.data.startswith("time_"))
async def cb_time(call: types.CallbackQuery, state: FSMContext):
    hour = int(call.data.replace("time_", ""))
    data = await state.get_data()

    await set_field(
        call.from_user.id,
        age_group=data["age"],
        problem=data["problem"],
        track=data["track"],
        send_hour=hour,
    )
    await state.update_data(hour=hour)

    # Запускаем бесплатный пробный период (дни 1–3 бесплатно)
    await set_field(call.from_user.id, day_num=1)
    await state.clear()
    await call.message.edit_text(
        f"🎁 *Первые 3 дня — бесплатно!*\n\n"
        f"Курс сформирован:\n"
        f"• Возраст: *{AGE_GROUPS[data['age']]}*\n"
        f"• Тема: *{PROBLEMS[data['problem']]}*\n"
        f"• Задание каждый день в *{hour}:00*\n\n"
        f"С 4-го дня курс стоит *{PRICE_RUB} ₽* — оплата только если понравится. Вот первое задание 👇"
    )
    await send_day(call.from_user.id, 1)
    await call.answer()

@dp.callback_query(Reg.pay, F.data == "check_pay")
async def cb_check_pay(call: types.CallbackQuery, state: FSMContext):
    user = await get_user(call.from_user.id)
    if not user or not user["payment_id"]:
        await call.answer("Платёж не найден. Попробуйте /start", show_alert=True)
        return

    paid = await check_payment(user["payment_id"])
    if paid:
        await activate_user(call.from_user.id)
        await state.clear()
        await call.message.edit_text(
            "✅ *Оплата подтверждена!*\n\n"
            "Добро пожаловать в курс «30 дней к чистой речи»!\n"
            "Вот ваше первое задание 👇"
        )
        await send_day(call.from_user.id, 1)
    else:
        await call.answer(
            "Платёж ещё не прошёл. Попробуйте через минуту или напишите нам.",
            show_alert=True,
        )

# ─── Команды для активных пользователей ──────────────────────────────────────
@dp.message(Command("today"))
async def cmd_today(msg: types.Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["paid"]:
        await msg.answer("Сначала нужно пройти регистрацию и оплатить курс. Напишите /start")
        return
    day = max(user["day_num"], 1)
    await send_day(msg.from_user.id, day)

@dp.message(Command("progress"))
async def cmd_progress(msg: types.Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["paid"]:
        await msg.answer("Курс ещё не начат. Напишите /start")
        return
    day = user["day_num"]
    pct = int(day / 30 * 100)
    bar = "▓" * (day // 3) + "░" * (10 - day // 3)
    await msg.answer(
        f"📊 *Ваш прогресс*\n\n"
        f"{bar} {pct}%\n"
        f"Пройдено: *{day} из 30 дней*\n\n"
        f"Следующее задание сегодня в *{user['send_hour']}:00*\n\n"
        f"Так держать! 💪",
        reply_markup=kb_support(),
    )

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(
        "*Команды бота:*\n\n"
        "/start — начать / перезапустить\n"
        "/today — получить задание прямо сейчас\n"
        "/progress — посмотреть прогресс\n"
        "/help — эта справка\n\n"
        "По любым вопросам — наш логопед всегда на связи:",
        reply_markup=kb_support(),
    )

@dp.callback_query(F.data.startswith("done_"))
async def cb_done(call: types.CallbackQuery):
    day = int(call.data.replace("done_", ""))
    user = await get_user(call.from_user.id)
    if user and user["paid"] and user["day_num"] == day:
        await set_field(call.from_user.id, day_num=day + 1)
        await call.message.edit_reply_markup(reply_markup=None)
        if day < 30:
            await call.answer(f"Отлично! День {day} засчитан ✅ Завтра пришлю день {day+1}!", show_alert=False)
        else:
            await call.answer("Поздравляем! Курс завершён! 🎉", show_alert=True)
    else:
        await call.answer()

# ─── Админ ───────────────────────────────────────────────────────────────────
@dp.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    total, paid = await count_all()
    await msg.answer(
        f"📈 *Статистика*\n\n"
        f"Всего пользователей: *{total}*\n"
        f"Оплатили курс: *{paid}*\n"
        f"Выручка: *{paid * PRICE_RUB} ₽*"
    )

@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.replace("/broadcast", "").strip()
    if not text:
        await msg.answer("Используйте: /broadcast текст сообщения")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_id FROM users WHERE paid=1") as cur:
            ids = [r[0] for r in await cur.fetchall()]
    ok, fail = 0, 0
    for tg_id in ids:
        try:
            await bot.send_message(tg_id, text)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.answer(f"Рассылка: ✅ {ok} / ❌ {fail}")

# ─── ЮKassa ──────────────────────────────────────────────────────────────────
async def create_payment(tg_id: int) -> tuple[str, str]:
    try:
        payment = Payment.create({
            "amount": {"value": str(PRICE_RUB) + ".00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://neurovershina.ru"},
            "capture": True,
            "description": f"Курс «30 дней к чистой речи» | tg:{tg_id}",
            "receipt": {"customer": {"email": f"tg{tg_id}@neurovershina.ru"}, "items": [{"description": "Kurs 30 dnej", "quantity": "1.00", "amount": {"value": str(PRICE_RUB)+".00", "currency": "RUB"}, "vat_code": 1, "payment_mode": "full_payment", "payment_subject": "service"}]}, "metadata": {"tg_id": str(tg_id)},
        }, str(uuid.uuid4()))
        return payment.confirmation.confirmation_url, payment.id
    except Exception as e:
        
        log.error(f"Payment error: {e} | {e.response.text if hasattr(e,'response') and e.response else 'no body'}")
        return SUPPORT_URL, "error"

async def check_payment(payment_id: str) -> bool:
    if payment_id == "error":
        return False
    try:
        p = Payment.find_one(payment_id)
        return p.status == "succeeded"
    except Exception as e:
        log.error(f"Payment check error: {e}")
        return False

# ─── Логика курса ────────────────────────────────────────────────────────────
async def activate_user(tg_id: int):
    await set_field(tg_id, paid=1, day_num=1, paid_at=datetime.now().isoformat())

async def send_day(tg_id: int, day: int):
    user = await get_user(tg_id)
    if not user:
        return
    track = user.get("track") or "general"
    ex = get_exercise(track, day)
    if not ex:
        return

    text = format_exercise(ex, day)
    try:
        await bot.send_message(tg_id, text, reply_markup=kb_next_day(day))
    except Exception as e:
        log.warning(f"Cannot send to {tg_id}: {e}")

# ─── Планировщик ─────────────────────────────────────────────────────────────

@dp.message(Command("free"))
async def cmd_free(msg: types.Message, state: FSMContext):
    """Тестовая активация без оплаты — только для администраторов."""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("⛔ Команда только для администраторов.")
        return
    user = await get_user(msg.from_user.id)
    if not user:
        await upsert_user(msg.from_user.id, msg.from_user.username or "")
        await set_field(msg.from_user.id, age_group="middle", problem="general", track="general", send_hour=9)
    await activate_user(msg.from_user.id)
    await state.clear()
    await msg.answer("✅ *Тестовая активация!*\n\nКурс активирован. Вот первое задание:")
    await send_day(msg.from_user.id, 1)

@dp.message(Command("demo"))
async def cmd_demo(msg: types.Message):
    """Карточка товара и страница оплаты для скриншотов ЮKassa."""
    # Сообщение 1 — карточка услуги с ценой
    await msg.answer(
        "🎓 *Курс «30 дней к чистой речи»*\n\n"
        "Персональная программа от нейропсихолога и логопеда:\n\n"
        "📌 *Что входит:*\n"
        "• 30 ежедневных заданий под возраст ребёнка\n"
        "• 3 трека: запуск речи, звук Р, общее развитие\n"
        "• Задание приходит в удобное вам время\n"
        "• Проверено логопедом\n\n"
        "👶 *Для детей 1.5 – 7 лет*\n\n"
        "💥 Первые *3 дня — бесплатно*\n"
        f"💳 Полный курс (30 дней) — *{PRICE_RUB} ₽*",
    )
    # Сообщение 2 — оформление заказа с кнопкой оплаты
    payment_url, payment_id = await create_payment(msg.from_user.id)
    await set_field(msg.from_user.id, payment_id=payment_id)
    await msg.answer(
        "✨ *Оформление заказа*\n\n"
        "┌ Курс: 30 дней к чистой речи\n"
        "├ Формат: ежедневные задания в Telegram\n"
        "├ Длительность: 30 дней\n"
        f"└ Стоимость: *{PRICE_RUB} ₽*\n\n"
        "После оплаты вы сразу получите первое задание 🎉",
        reply_markup=kb_pay(payment_url),
    )

async def daily_job():
    """Запускается каждый час — рассылает задания нужным пользователям."""
    now_hour = datetime.now().hour
    users = await get_all_active()
    for u in users:
        if u["send_hour"] == now_hour:
            if not u["paid"] and u["day_num"] >= 4:
                # Пробный период закончился — предлагаем оплатить
                payment_url, payment_id = await create_payment(u["tg_id"])
                await set_field(u["tg_id"], payment_id=payment_id)
                try:
                    await bot.send_message(
                        u["tg_id"],
                        f"🎓 *3 бесплатных дня позади!*\n\n"
                        f"Надеемся, упражнения уже помогают 💪\n\n"
                        f"Чтобы продолжить курс (ещё 27 дней), оформите доступ — всего *{PRICE_RUB} ₽*.",
                        reply_markup=kb_pay(payment_url),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    log.warning(f"Cannot send payment to {u['tg_id']}: {e}")
            else:
                await send_day(u["tg_id"], u["day_num"])
                # Инкрементируем день
                if u["day_num"] < 30:
                    await set_field(u["tg_id"], day_num=u["day_num"] + 1)
            await asyncio.sleep(0.05)

# ─── Запуск ──────────────────────────────────────────────────────────────────
async def main():
    await init_db()
    scheduler.add_job(daily_job, "cron", minute=0)  # каждый час в 00 минут
    scheduler.start()
    log.info("Bot started")
    await bot.set_my_commands([
        BotCommand(command="start",    description="🏠 Начать / главное меню"),
        BotCommand(command="today",    description="📚 Задание на сегодня"),
        BotCommand(command="progress",  description="📊 Мой прогресс"),
        BotCommand(command="help",      description="❓ Помощь"),
        BotCommand(command="demo",      description="📋 Карточка услуги и оплата"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
