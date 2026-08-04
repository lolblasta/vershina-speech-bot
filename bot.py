"""
30 Ð´Ð½ÐµÐ¹ Ðº ÑÐ¸ÑÑÐ¾Ð¹ ÑÐµÑÐ¸ â Telegram-Ð±Ð¾Ñ
ÐÐµÑÑÐºÐ¸Ð¹ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´Ð¸ÑÐµÑÐºÐ¸Ð¹ ÑÐµÐ½ÑÑ Â«ÐÐµÑÑÐ¸Ð½Ð°Â»
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
from aiogram.types import (
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

# âââ ÐÐ¾Ð½ÑÐ¸Ð³ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

bot       = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp        = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# âââ FSM âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
class Reg(StatesGroup):
    age     = State()
    problem = State()
    time    = State()
    pay     = State()

# âââ ÐÐ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
            "SELECT * FROM users WHERE paid=1 AND day_num < 30"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def count_all() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE paid=1") as c:
            paid = (await c.fetchone())[0]
    return total, paid

# âââ ÐÐ»Ð°Ð²Ð¸Ð°ÑÑÑÑ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def kb_age() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ð¶ 1.5â3 Ð³Ð¾Ð´Ð°",  callback_data="age_young")],
        [InlineKeyboardButton(text="ð§ 3â5 Ð»ÐµÑ",     callback_data="age_middle")],
        [InlineKeyboardButton(text="ð§ 5â7 Ð»ÐµÑ",     callback_data="age_older")],
    ])

def kb_problem() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ð¬ ÐÐ°Ð»Ð¾ Ð³Ð¾Ð²Ð¾ÑÐ¸Ñ / Ð½Ðµ Ð³Ð¾Ð²Ð¾ÑÐ¸Ñ",     callback_data="prob_launch")],
        [InlineKeyboardButton(text="ð¤ ÐÐµ Ð¿ÑÐ¾Ð¸Ð·Ð½Ð¾ÑÐ¸Ñ Ð·Ð²ÑÐº Ð ",           callback_data="prob_sound_r")],
        [InlineKeyboardButton(text="ð Ð¥Ð¾ÑÑ ÑÐ°Ð·Ð²Ð¸ÑÑ ÑÐµÑÑ Ð² ÑÐµÐ»Ð¾Ð¼",      callback_data="prob_general")],
    ])

def kb_time() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="âï¸ 8:00",   callback_data="time_8"),
         InlineKeyboardButton(text="ð¤ 9:00",   callback_data="time_9")],
        [InlineKeyboardButton(text="ð 10:00",  callback_data="time_10"),
         InlineKeyboardButton(text="ð 12:00",  callback_data="time_12")],
        [InlineKeyboardButton(text="ð 18:00",  callback_data="time_18"),
         InlineKeyboardButton(text="ð 20:00",  callback_data="time_20")],
    ])

def kb_pay(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"ð³ ÐÐ¿Ð»Ð°ÑÐ¸ÑÑ {PRICE_RUB} â½", url=url)],
        [InlineKeyboardButton(text="â Ð¯ Ð¾Ð¿Ð»Ð°ÑÐ¸Ð» â Ð¿ÑÐ¾Ð²ÐµÑÐ¸ÑÑ", callback_data="check_pay")],
        [InlineKeyboardButton(text="â ÐÐ¾Ð¿ÑÐ¾Ñ / Ð¿Ð¾Ð¼Ð¾ÑÑ", url=SUPPORT_URL)],
    ])

def kb_support() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ð¬ ÐÐ°Ð¿Ð¸ÑÐ°ÑÑ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´Ñ", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="ð¢ ÐÐ°Ñ Telegram-ÐºÐ°Ð½Ð°Ð»", url=CHANNEL_URL)],
    ])

def kb_next_day(day: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="â Ð¡Ð´ÐµÐ»Ð°Ð»Ð¸! ÐÐ°Ð²ÑÑÐ° Ð¶Ð´Ñ ÑÐ»ÐµÐ´ÑÑÑÐµÐµ",
                              callback_data=f"done_{day}")],
        [InlineKeyboardButton(text="â ÐÐ¾Ð¿ÑÐ¾Ñ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´Ñ", url=SUPPORT_URL)],
    ])

# âââ Ð¥ÐµÐ½Ð´Ð»ÐµÑÑ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await upsert_user(msg.from_user.id, msg.from_user.username or "")
    user = await get_user(msg.from_user.id)

    if user and user["paid"] and user["day_num"] > 0:
        await msg.answer(
            f"Ð¡ Ð²Ð¾Ð·Ð²ÑÐ°ÑÐµÐ½Ð¸ÐµÐ¼! ð\n\n"
            f"Ð¢Ñ Ð½Ð° *Ð´Ð½Ðµ {user['day_num']} Ð¸Ð· 30*. "
            f"Ð¡Ð»ÐµÐ´ÑÑÑÐµÐµ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ Ð¿ÑÐ¸Ð´ÑÑ Ð² {user['send_hour']}:00.\n\n"
            f"Ð¥Ð¾ÑÐµÑÑ Ð¿Ð¾Ð»ÑÑÐ¸ÑÑ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ Ð¿ÑÑÐ¼Ð¾ ÑÐµÐ¹ÑÐ°Ñ? ÐÐ°Ð¿Ð¸ÑÐ¸ /today",
        )
        return

    await state.clear()
    await msg.answer(
        "ð ÐÑÐ¸Ð²ÐµÑ! Ð­ÑÐ¾ Ð±Ð¾Ñ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´Ð¸ÑÐµÑÐºÐ¾Ð³Ð¾ ÑÐµÐ½ÑÑÐ° *Â«ÐÐµÑÑÐ¸Ð½Ð°Â»*.\n\n"
        "ÐÐ´ÐµÑÑ Ð²Ñ Ð¿Ð¾Ð»ÑÑÐ¸ÑÐµ *30 ÐµÐ¶ÐµÐ´Ð½ÐµÐ²Ð½ÑÑ Ð·Ð°Ð´Ð°Ð½Ð¸Ð¹* Ð´Ð»Ñ ÑÐ°Ð·Ð²Ð¸ÑÐ¸Ñ ÑÐµÑÐ¸ ÑÐµÐ±ÑÐ½ÐºÐ° â "
        "ÐºÐ¾ÑÐ¾ÑÐºÐ¸Ñ, Ð¿ÑÐ°ÐºÑÐ¸ÑÐ½ÑÑ Ð¸ Ð¿ÑÐ¾Ð²ÐµÑÐµÐ½Ð½ÑÑ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´Ð¾Ð¼.\n\n"
        "ÐÐ»Ñ Ð½Ð°ÑÐ°Ð»Ð° ÑÐºÐ°Ð¶Ð¸ÑÐµ: ÑÐºÐ¾Ð»ÑÐºÐ¾ Ð»ÐµÑ Ð²Ð°ÑÐµÐ¼Ñ ÑÐµÐ±ÑÐ½ÐºÑ?",
        reply_markup=kb_age(),
    )
    await state.set_state(Reg.age)

@dp.callback_query(Reg.age, F.data.startswith("age_"))
async def cb_age(call: types.CallbackQuery, state: FSMContext):
    age = call.data.replace("age_", "")
    await state.update_data(age=age)
    await call.message.edit_text(
        f"ÐÑÐ»Ð¸ÑÐ½Ð¾! ÐÐ¾Ð·ÑÐ°ÑÑ: *{AGE_GROUPS[age]}*\n\n"
        "Ð§ÑÐ¾ Ð²Ð°Ñ Ð±ÐµÑÐ¿Ð¾ÐºÐ¾Ð¸Ñ Ð±Ð¾Ð»ÑÑÐµ Ð²ÑÐµÐ³Ð¾?",
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

    # ÐÑÐ»Ð¸ Ð²ÑÐ±ÑÐ°Ð» Ð , Ð½Ð¾ ÑÐµÐ±ÑÐ½ÐºÑ < 3 Ð»ÐµÑ â Ð¼ÑÐ³ÐºÐ¾ Ð¿ÐµÑÐµÐºÐ»ÑÑÐ°ÐµÐ¼
    note = ""
    if problem == "sound_r" and age == "young":
        note = (
            "\n\n_ÐÐ²ÑÐº Ð  Ð¾Ð±ÑÑÐ½Ð¾ ÑÐ¾ÑÐ¼Ð¸ÑÑÐµÑÑÑ Ð¿Ð¾ÑÐ»Ðµ 5 Ð»ÐµÑ. "
            "ÐÐ»Ñ Ð²Ð°ÑÐµÐ³Ð¾ Ð²Ð¾Ð·ÑÐ°ÑÑÐ° Ð¿Ð¾Ð´Ð±ÐµÑÑÐ¼ ÑÑÐµÐº Ð½Ð° Ð·Ð°Ð¿ÑÑÐº Ð¸ ÑÐ°Ð·Ð²Ð¸ÑÐ¸Ðµ ÑÐµÑÐ¸ Ð² ÑÐµÐ»Ð¾Ð¼._"
        )

    await state.update_data(problem=problem, track=track)
    await call.message.edit_text(
        f"ÐÐ¾Ð½ÑÐ»! Ð¢ÐµÐ¼Ð°: *{PROBLEMS[problem]}*{note}\n\n"
        "Ð ÐºÐ°ÐºÐ¾Ðµ Ð²ÑÐµÐ¼Ñ Ð²Ð°Ð¼ ÑÐ´Ð¾Ð±Ð½Ð¾ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ ÐµÐ¶ÐµÐ´Ð½ÐµÐ²Ð½Ð¾Ðµ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ?",
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

    # Ð¡Ð¾Ð·Ð´Ð°ÑÐ¼ Ð¿Ð»Ð°ÑÑÐ¶ Ð² Ð®Kassa
    payment_url, payment_id = await create_payment(call.from_user.id)
    await set_field(call.from_user.id, payment_id=payment_id)

    await call.message.edit_text(
        f"â¨ *ÐÑÑ Ð³Ð¾ÑÐ¾Ð²Ð¾!*\n\n"
        f"ÐÐ°Ñ Ð¿ÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐ¹ ÐºÑÑÑ ÑÑÐ¾ÑÐ¼Ð¸ÑÐ¾Ð²Ð°Ð½:\n"
        f"â¢ ÐÐ¾Ð·ÑÐ°ÑÑ: *{AGE_GROUPS[data['age']]}*\n"
        f"â¢ Ð¢ÐµÐ¼Ð°: *{PROBLEMS[data['problem']]}*\n"
        f"â¢ ÐÐ°Ð´Ð°Ð½Ð¸Ðµ ÐºÐ°Ð¶Ð´ÑÐ¹ Ð´ÐµÐ½Ñ Ð² *{hour}:00*\n\n"
        f"Ð¡ÑÐ¾Ð¸Ð¼Ð¾ÑÑÑ Ð¿Ð¾Ð»Ð½Ð¾Ð³Ð¾ ÐºÑÑÑÐ° (30 Ð´Ð½ÐµÐ¹) â *{PRICE_RUB} â½*\n\n"
        f"ÐÐ¾ÑÐ»Ðµ Ð¾Ð¿Ð»Ð°ÑÑ Ð²Ñ ÑÑÐ°Ð·Ñ Ð¿Ð¾Ð»ÑÑÐ¸ÑÐµ Ð¿ÐµÑÐ²Ð¾Ðµ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ ð",
        reply_markup=kb_pay(payment_url),
    )
    await state.set_state(Reg.pay)
    await call.answer()

@dp.callback_query(Reg.pay, F.data == "check_pay")
async def cb_check_pay(call: types.CallbackQuery, state: FSMContext):
    user = await get_user(call.from_user.id)
    if not user or not user["payment_id"]:
        await call.answer("ÐÐ»Ð°ÑÑÐ¶ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½. ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ /start", show_alert=True)
        return

    paid = await check_payment(user["payment_id"])
    if paid:
        await activate_user(call.from_user.id)
        await state.clear()
        await call.message.edit_text(
            "â *ÐÐ¿Ð»Ð°ÑÐ° Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð°!*\n\n"
            "ÐÐ¾Ð±ÑÐ¾ Ð¿Ð¾Ð¶Ð°Ð»Ð¾Ð²Ð°ÑÑ Ð² ÐºÑÑÑ Â«30 Ð´Ð½ÐµÐ¹ Ðº ÑÐ¸ÑÑÐ¾Ð¹ ÑÐµÑÐ¸Â»!\n"
            "ÐÐ¾Ñ Ð²Ð°ÑÐµ Ð¿ÐµÑÐ²Ð¾Ðµ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ ð"
        )
        await send_day(call.from_user.id, 1)
    else:
        await call.answer(
            "ÐÐ»Ð°ÑÑÐ¶ ÐµÑÑ Ð½Ðµ Ð¿ÑÐ¾ÑÑÐ». ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ ÑÐµÑÐµÐ· Ð¼Ð¸Ð½ÑÑÑ Ð¸Ð»Ð¸ Ð½Ð°Ð¿Ð¸ÑÐ¸ÑÐµ Ð½Ð°Ð¼.",
            show_alert=True,
        )

# âââ ÐÐ¾Ð¼Ð°Ð½Ð´Ñ Ð´Ð»Ñ Ð°ÐºÑÐ¸Ð²Ð½ÑÑ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹ ââââââââââââââââââââââââââââââââââââââ
@dp.message(Command("today"))
async def cmd_today(msg: types.Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["paid"]:
        await msg.answer("Ð¡Ð½Ð°ÑÐ°Ð»Ð° Ð½ÑÐ¶Ð½Ð¾ Ð¿ÑÐ¾Ð¹ÑÐ¸ ÑÐµÐ³Ð¸ÑÑÑÐ°ÑÐ¸Ñ Ð¸ Ð¾Ð¿Ð»Ð°ÑÐ¸ÑÑ ÐºÑÑÑ. ÐÐ°Ð¿Ð¸ÑÐ¸ÑÐµ /start")
        return
    day = max(user["day_num"], 1)
    await send_day(msg.from_user.id, day)

@dp.message(Command("progress"))
async def cmd_progress(msg: types.Message):
    user = await get_user(msg.from_user.id)
    if not user or not user["paid"]:
        await msg.answer("ÐÑÑÑ ÐµÑÑ Ð½Ðµ Ð½Ð°ÑÐ°Ñ. ÐÐ°Ð¿Ð¸ÑÐ¸ÑÐµ /start")
        return
    day = user["day_num"]
    pct = int(day / 30 * 100)
    bar = "â" * (day // 3) + "â" * (10 - day // 3)
    await msg.answer(
        f"ð *ÐÐ°Ñ Ð¿ÑÐ¾Ð³ÑÐµÑÑ*\n\n"
        f"{bar} {pct}%\n"
        f"ÐÑÐ¾Ð¹Ð´ÐµÐ½Ð¾: *{day} Ð¸Ð· 30 Ð´Ð½ÐµÐ¹*\n\n"
        f"Ð¡Ð»ÐµÐ´ÑÑÑÐµÐµ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ ÑÐµÐ³Ð¾Ð´Ð½Ñ Ð² *{user['send_hour']}:00*\n\n"
        f"Ð¢Ð°Ðº Ð´ÐµÑÐ¶Ð°ÑÑ! ðª",
        reply_markup=kb_support(),
    )

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(
        "*ÐÐ¾Ð¼Ð°Ð½Ð´Ñ Ð±Ð¾ÑÐ°:*\n\n"
        "/start â Ð½Ð°ÑÐ°ÑÑ / Ð¿ÐµÑÐµÐ·Ð°Ð¿ÑÑÑÐ¸ÑÑ\n"
        "/today â Ð¿Ð¾Ð»ÑÑÐ¸ÑÑ Ð·Ð°Ð´Ð°Ð½Ð¸Ðµ Ð¿ÑÑÐ¼Ð¾ ÑÐµÐ¹ÑÐ°Ñ\n"
        "/progress â Ð¿Ð¾ÑÐ¼Ð¾ÑÑÐµÑÑ Ð¿ÑÐ¾Ð³ÑÐµÑÑ\n"
        "/help â ÑÑÐ° ÑÐ¿ÑÐ°Ð²ÐºÐ°\n\n"
        "ÐÐ¾ Ð»ÑÐ±ÑÐ¼ Ð²Ð¾Ð¿ÑÐ¾ÑÐ°Ð¼ â Ð½Ð°Ñ Ð»Ð¾Ð³Ð¾Ð¿ÐµÐ´ Ð²ÑÐµÐ³Ð´Ð° Ð½Ð° ÑÐ²ÑÐ·Ð¸:",
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
            await call.answer(f"ÐÑÐ»Ð¸ÑÐ½Ð¾! ÐÐµÐ½Ñ {day} Ð·Ð°ÑÑÐ¸ÑÐ°Ð½ â ÐÐ°Ð²ÑÑÐ° Ð¿ÑÐ¸ÑÐ»Ñ Ð´ÐµÐ½Ñ {day+1}!", show_alert=False)
        else:
            await call.answer("ÐÐ¾Ð·Ð´ÑÐ°Ð²Ð»ÑÐµÐ¼! ÐÑÑÑ Ð·Ð°Ð²ÐµÑÑÑÐ½! ð", show_alert=True)
    else:
        await call.answer()

# âââ ÐÐ´Ð¼Ð¸Ð½ âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
@dp.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    total, paid = await count_all()
    await msg.answer(
        f"ð *Ð¡ÑÐ°ÑÐ¸ÑÑÐ¸ÐºÐ°*\n\n"
        f"ÐÑÐµÐ³Ð¾ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹: *{total}*\n"
        f"ÐÐ¿Ð»Ð°ÑÐ¸Ð»Ð¸ ÐºÑÑÑ: *{paid}*\n"
        f"ÐÑÑÑÑÐºÐ°: *{paid * PRICE_RUB} â½*"
    )

@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.replace("/broadcast", "").strip()
    if not text:
        await msg.answer("ÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ: /broadcast ÑÐµÐºÑÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ")
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
    await msg.answer(f"Ð Ð°ÑÑÑÐ»ÐºÐ°: â {ok} / â {fail}")

# âââ Ð®Kassa ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def create_payment(tg_id: int) -> tuple[str, str]:
    try:
        payment = Payment.create({
            "amount": {"value": str(PRICE_RUB) + ".00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": f"https://t.me/neyro_vershina_bot"},
            "capture": True,
            "description": f"ÐÑÑÑ Â«30 Ð´Ð½ÐµÐ¹ Ðº ÑÐ¸ÑÑÐ¾Ð¹ ÑÐµÑÐ¸Â» | tg:{tg_id}",
            "metadata": {"tg_id": str(tg_id)},
        }, str(uuid.uuid4()))
        return payment.confirmation.confirmation_url, payment.id
    except Exception as e:
        log.error(f"Payment creation error: {e}")
        # Fallback â Ð¾ÑÐ¿ÑÐ°Ð²Ð¸ÑÑ Ð½Ð° ÑÑÑÐ°Ð½Ð¸ÑÑ Ð¿Ð¾Ð´Ð´ÐµÑÐ¶ÐºÐ¸
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

# âââ ÐÐ¾Ð³Ð¸ÐºÐ° ÐºÑÑÑÐ° ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# âââ ÐÐ»Ð°Ð½Ð¸ÑÐ¾Ð²ÑÐ¸Ðº âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@dp.message(Command("free"))
async def cmd_free(msg: types.Message, state: FSMContext):
    """Тестовая активация без оплаты — только для администраторов."""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("Команда только для администраторов.")
        return
    user = await get_user(msg.from_user.id)
    if not user:
        await upsert_user(msg.from_user.id, msg.from_user.username or "")
        await set_field(msg.from_user.id, age_group="middle", problem="general", track="general", send_hour=9)
    await activate_user(msg.from_user.id)
    await state.clear()
    await msg.answer("✅ *Тестовая активация!*\n\nКурс активирован бесплатно. Вот первое задание:")
    await send_day(msg.from_user.id, 1)

async def daily_job():
    """ÐÐ°Ð¿ÑÑÐºÐ°ÐµÑÑÑ ÐºÐ°Ð¶Ð´ÑÐ¹ ÑÐ°Ñ â ÑÐ°ÑÑÑÐ»Ð°ÐµÑ Ð·Ð°Ð´Ð°Ð½Ð¸Ñ Ð½ÑÐ¶Ð½ÑÐ¼ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÑÐ¼."""
    now_hour = datetime.now().hour
    users = await get_all_active()
    for u in users:
        if u["send_hour"] == now_hour:
            await send_day(u["tg_id"], u["day_num"])
            # ÐÐ½ÐºÑÐµÐ¼ÐµÐ½ÑÐ¸ÑÑÐµÐ¼ Ð´ÐµÐ½Ñ (ÐµÑÐ»Ð¸ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ Ð½Ðµ Ð½Ð°Ð¶Ð°Ð» Â«Ð¡Ð´ÐµÐ»Ð°Ð»Ð¸Â» ÑÐ°Ð¼)
            if u["day_num"] < 30:
                await set_field(u["tg_id"], day_num=u["day_num"] + 1)
            await asyncio.sleep(0.05)

# âââ ÐÐ°Ð¿ÑÑÐº ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
async def main():
    await init_db()
    scheduler.add_job(daily_job, "cron", minute=0)  # ÐºÐ°Ð¶Ð´ÑÐ¹ ÑÐ°Ñ Ð² 00 Ð¼Ð¸Ð½ÑÑ
    scheduler.start()
    log.info("Bot started")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
