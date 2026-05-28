"""Fasting reminder bot.

Stateless from Telegram's perspective: every state lives in SQLite (db.py).
Polls Telegram via long-polling (no webhook → no HTTPS needed on host).
Background scheduler ticks every 60 s and fires due notifications.

Commands:
    /start    — welcome + inline buttons to start any protocol
    /16 /18 /20 /omad — start a fasting protocol
    /status   — show current state + time remaining
    /stop     — abort active timer manually
    /help     — usage

Inline buttons mirror the slash commands so the user never has to type.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                            InlineKeyboardMarkup, LabeledPrice, Message,
                            PreCheckoutQuery)
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import sync_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fasting-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN env var is required")

MINI_APP_URL = os.environ.get(
    "MINI_APP_URL", "https://rossanoua.github.io/fasting-app/"
).strip()

SYNC_API_PORT = int(os.environ.get("SYNC_API_PORT", "8080"))
SYNC_API_HOST = os.environ.get("SYNC_API_HOST", "0.0.0.0")

PROTOCOLS = {
    "16": (16, 8, "16:8 — класика, 16 год голодування + 8 год вікно"),
    "18": (18, 6, "18:6 — 18 год голодування + 6 год вікно"),
    "20": (20, 4, "20:4 — Warrior, 20 год голодування + 4 год вікно"),
    "omad": (23, 1, "OMAD — один прийом їжі на день (23:1)"),
}

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ─── Helpers ──────────────────────────────────────────────────────────

def now_ts() -> int:
    return int(time.time())


def fmt_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "вже"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h > 0:
        return f"{h} год {m} хв"
    return f"{m} хв"


def protocol_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="16:8", callback_data="proto:16"),
            InlineKeyboardButton(text="18:6", callback_data="proto:18"),
        ],
        [
            InlineKeyboardButton(text="20:4", callback_data="proto:20"),
            InlineKeyboardButton(text="OMAD", callback_data="proto:omad"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="status"),
            InlineKeyboardButton(text="⏹ Зупинити", callback_data="stop"),
        ],
        [
            InlineKeyboardButton(text="🕐 Відкрити трекер", url=MINI_APP_URL),
        ],
    ])


# ─── Handlers ─────────────────────────────────────────────────────────

@dp.message(CommandStart(deep_link=True))
async def cmd_start_with_param(msg: Message) -> None:
    # Deep-link payload e.g. "/start donate" comes from Mini App donation button
    arg = (msg.text or "").split(maxsplit=1)
    payload = arg[1].strip() if len(arg) > 1 else ""
    if payload == "donate":
        await cmd_donate(msg)
        return
    # Unknown payload — fall through to default welcome
    await cmd_start(msg)


@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await msg.answer(
        "👋 Привіт! Я нагадую коли починати/завершувати інтервальне "
        "голодування.\n\nОбери протокол:",
        reply_markup=protocol_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(
        "<b>Команди:</b>\n"
        "/start — обрати протокол\n"
        "/16 /18 /20 /omad — стартонути напряму\n"
        "/status — поточний стан\n"
        "/stop — припинити поточний таймер\n"
        "/can — що можна/не можна під час голодування\n"
        "/donate — підтримати розробку (Telegram Stars)\n"
        "/help — ця довідка\n\n"
        f"<b>Візуальний таймер:</b> {MINI_APP_URL}",
        reply_markup=protocol_keyboard(),
    )


# ─── Donation: Telegram Stars (XTR) ───────────────────────────────────

DONATION_OPTIONS = [
    (50, "Кава автору ☕"),
    (150, "Місяць хостингу 🌱"),
    (500, "Велика підтримка ❤️"),
]


def donate_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"⭐ {n} — {label}",
                                    callback_data=f"donate:{n}")]
            for n, label in DONATION_OPTIONS]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("donate"))
async def cmd_donate(msg: Message) -> None:
    await msg.answer(
        "💛 <b>Дякую що думаєш про підтримку!</b>\n\n"
        "Цей бот — open source, безкоштовний і таким залишиться. "
        "Якщо хочеш покрити частину витрат на хостинг або просто "
        "сказати «дякую» — обери суму в Telegram Stars:\n\n"
        "<i>Stars йдуть напряму через Telegram, без посередників, "
        "без передачі карткових даних. Виводяться у TON crypto автору.</i>",
        reply_markup=donate_keyboard(),
    )


@dp.callback_query(F.data.startswith("donate:"))
async def cb_donate(cb: CallbackQuery) -> None:
    amount = int(cb.data.split(":", 1)[1])
    label_match = next((l for n, l in DONATION_OPTIONS if n == amount),
                       "Підтримка")
    await cb.message.answer_invoice(
        title="Підтримка fasting tracker",
        description=label_match,
        payload=f"donate-{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} ⭐", amount=amount)],
        # provider_token can be empty for Stars (XTR currency)
        provider_token="",
    )
    await cb.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    # Always confirm — payment validation is handled by Telegram for Stars
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(msg: Message) -> None:
    amount = msg.successful_payment.total_amount
    await msg.answer(
        f"❤️ Дякую за {amount} ⭐! Це реально допомагає тримати бот живим.",
    )
    log.info(f"successful donation: user={msg.from_user.id} amount={amount}")


@dp.message(Command("can"))
async def cmd_can(msg: Message) -> None:
    await msg.answer(
        "<b>✅ Можна (не розриває піст):</b>\n"
        "• Вода (звичайна, мінеральна, газована без додавань)\n"
        "• Чорна кава 1–3 чашки\n"
        "• Чай зелений/чорний/трав'яний — без молока, цукру, меду\n"
        "• Електроліти без калорій (сіль, магній)\n\n"
        "<b>⚠️ Спірно (залежить від цілі):</b>\n"
        "• Стевія, аспартам, сукралоза — ОК для weight loss, ❌ для аутофагії\n"
        "• Яблучний оцет 1–2 ч.л.\n"
        "• Дієтична кола / «zero» напої\n\n"
        "<b>❌ Не можна (розриває піст):</b>\n"
        "• Молоко/вершки/рослинне молоко\n"
        "• Цукор, мед, сиропи\n"
        "• Соки, фруктові води, смузі\n"
        "• Кістковий бульйон, BCAA, протеїнові коктейлі\n"
        "• Bulletproof/MCT кава\n"
        "• Будь-які перекуси, навіть «трошки»\n\n"
        f"Деталі + цілі — у Mini App: {MINI_APP_URL}\n"
        "<i>Не медична порада.</i>",
    )


@dp.message(Command("16", "18", "20", "omad"))
async def cmd_protocol(msg: Message) -> None:
    cmd = msg.text.lstrip("/").lower().split("@")[0]
    await start_protocol(msg.from_user.id, msg.chat.id, cmd, msg)


@dp.callback_query(F.data.startswith("proto:"))
async def cb_protocol(cb: CallbackQuery) -> None:
    proto = cb.data.split(":", 1)[1]
    await start_protocol(cb.from_user.id, cb.message.chat.id, proto, cb.message)
    await cb.answer()


async def start_protocol(user_id: int, chat_id: int, proto_key: str,
                          message: Message) -> None:
    if proto_key not in PROTOCOLS:
        await message.answer(f"Невідомий протокол: {proto_key}")
        return
    target_hours, eating_hours, descr = PROTOCOLS[proto_key]
    await db.start_fast(user_id, chat_id, target_hours, eating_hours, now_ts())
    await message.answer(
        f"✅ Старт <b>{descr}</b>.\n\n"
        f"⏰ Нагадаю через <b>{target_hours} год</b> коли можна їсти.\n"
        f"🍽 Потім через <b>{eating_hours} год</b> — коли пора голодувати знову.",
        reply_markup=protocol_keyboard(),
    )


@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    await send_status(msg.from_user.id, msg.chat.id)


@dp.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery) -> None:
    await send_status(cb.from_user.id, cb.message.chat.id)
    await cb.answer()


async def send_status(user_id: int, chat_id: int) -> None:
    state = await db.get_status(user_id)
    if not state or (not state.get("fast_start_ts") and not state.get("eating_start_ts")):
        await bot.send_message(chat_id, "Активного таймера нема. Натисни кнопку щоб почати.",
                                reply_markup=protocol_keyboard())
        return

    now = now_ts()
    parts = []
    if state.get("fast_start_ts"):
        elapsed = now - state["fast_start_ts"]
        target_s = state["target_hours"] * 3600
        if elapsed < target_s:
            parts.append(
                f"🔵 <b>Голодуєш</b> — пройшло {fmt_remaining(elapsed)}, "
                f"до завершення {fmt_remaining(target_s - elapsed)} "
                f"(протокол {state['target_hours']}:{state['eating_hours']})"
            )
        else:
            parts.append(
                f"✅ <b>Голодування завершено</b> — понад ціль "
                f"{fmt_remaining(elapsed - target_s)} "
                f"(протокол {state['target_hours']}:{state['eating_hours']})"
            )
    if state.get("eating_start_ts"):
        elapsed = now - state["eating_start_ts"]
        target_s = state["eating_hours"] * 3600
        if elapsed < target_s:
            parts.append(
                f"🍽 <b>Вікно їжі</b> — пройшло {fmt_remaining(elapsed)}, "
                f"до завершення {fmt_remaining(target_s - elapsed)}"
            )
    await bot.send_message(chat_id, "\n\n".join(parts) or "Активного таймера нема.",
                            reply_markup=protocol_keyboard())


@dp.message(Command("stop"))
async def cmd_stop(msg: Message) -> None:
    await stop_user(msg.from_user.id, msg.chat.id)


@dp.callback_query(F.data == "stop")
async def cb_stop(cb: CallbackQuery) -> None:
    await stop_user(cb.from_user.id, cb.message.chat.id)
    await cb.answer()


async def stop_user(user_id: int, chat_id: int) -> None:
    await db.stop_all(user_id)
    await bot.send_message(chat_id, "⏹ Таймери очищено.",
                            reply_markup=protocol_keyboard())


# ─── Scheduler: due-reminder check ────────────────────────────────────

async def reminder_tick() -> None:
    now = now_ts()
    # Fasting target reached → "ти можеш їсти"
    for u in await db.all_due_for_fast_notification(now):
        try:
            await bot.send_message(
                u["chat_id"],
                f"🎯 <b>Голодування завершено!</b>\n\n"
                f"Можеш починати їсти. Вікно — {u['eating_hours']} год.\n"
                f"Нагадаю коли пора знову голодувати."
            )
            await db.mark_fast_notified(u["user_id"], now)
            log.info(f"sent fast-done notification to user {u['user_id']}")
        except Exception as e:
            log.error(f"failed to notify user {u['user_id']}: {e}")

    # Eating window ended → "пора голодувати"
    for u in await db.all_due_for_eating_notification(now):
        try:
            await bot.send_message(
                u["chat_id"],
                f"⏰ <b>Вікно зачинилось — пора голодувати.</b>\n\n"
                f"Натисни кнопку щоб стартонути новий протокол:",
                reply_markup=protocol_keyboard(),
            )
            await db.mark_eating_notified(u["user_id"])
            log.info(f"sent eating-ended notification to user {u['user_id']}")
        except Exception as e:
            log.error(f"failed to notify user {u['user_id']}: {e}")


# ─── Main ─────────────────────────────────────────────────────────────

async def main() -> None:
    await db.init()

    # Background tick: check due reminders every 60s
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminder_tick, "interval", seconds=60, max_instances=1)
    scheduler.start()

    # HTTP sync API (Mini App ↔ bot)
    sync_app = sync_api.build_app(BOT_TOKEN)
    runner = web.AppRunner(sync_app)
    await runner.setup()
    site = web.TCPSite(runner, SYNC_API_HOST, SYNC_API_PORT)
    await site.start()
    log.info(f"sync API on {SYNC_API_HOST}:{SYNC_API_PORT}")

    log.info("scheduler started, polling Telegram...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
