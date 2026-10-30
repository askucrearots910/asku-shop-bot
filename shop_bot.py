import asyncio
import html
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
REVIEWS_URL = os.environ.get("REVIEWS_URL", "")

# Реквизиты для оплаты по СБП (настраиваются через переменные окружения на хостинге)
SBP_PHONE = os.environ.get("SBP_PHONE", "+7 (900) 000-00-00")
SBP_BANK = os.environ.get("SBP_BANK", "Т-Банк / Сбер")
RECIPIENT_NAME = os.environ.get("RECIPIENT_NAME", "Имя Отчество")

# ---------------------------------------------------------------------------
# КАТАЛОГ И ТОВАРЫ (Цены со скидкой до 5 ноября)
# ---------------------------------------------------------------------------
CATEGORIES = {
    "cf_ios": "📱 Counter Flame (iOS)",
}

PRODUCTS = {
    "cf_1d": {
        "cat": "cf_ios",
        "title": "Counter Flame — 1 День",
        "desc": "Доступ к софту на 24 часа. Установка через .ipa файл.",
        "price": 160,
    },
    "cf_7d": {
        "cat": "cf_ios",
        "title": "Counter Flame — 7 Дней",
        "desc": "Доступ к софту на 7 дней.",
        "price": 800,
    },
    "cf_30d": {
        "cat": "cf_ios",
        "title": "Counter Flame — 30 Дней (+ Бонус)",
        "desc": "Доступ на 30 дней. Бонус: индивидуальный сертификат в подарок!",
        "price": 1650,
    },
}

# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------
db = sqlite3.connect("shop.db")
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        key_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'free',
        buyer_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id TEXT,
        key_id INTEGER,
        status TEXT DEFAULT 'pending',
        created TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """
)
db.commit()


def stock(pid: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS n FROM keys WHERE product_id=? AND status='free'", (pid,)
    ).fetchone()
    return row["n"]


def get_free_key(pid: str):
    return db.execute(
        "SELECT id, key_text FROM keys WHERE product_id=? AND status='free' ORDER BY id LIMIT 1",
        (pid,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------
def categories_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cid, name in CATEGORIES.items():
        kb.button(text=name, callback_data=f"cat:{cid}")
    kb.adjust(1)
    return kb.as_markup()


router = Router()


# ---------------------------------------------------------------------------
# Покупатель
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def start(m: Message):
    name = html.escape(m.from_user.first_name or "друг")
    await m.answer(
        f"Привет, {name}! 👋\nДобро пожаловать в магазин **ASKU**.\nВыбери товар для покупки:",
        reply_markup=categories_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    await c.message.edit_text("Выбери товар для покупки:", reply_markup=categories_kb())
    await c.answer()


@router.callback_query(F.data.startswith("cat:"))
async def show_category(c: CallbackQuery):
    cat = c.data.split(":", 1)[1]
    if cat not in CATEGORIES:
        return await c.answer("Раздел не найден", show_alert=True)
    kb = InlineKeyboardBuilder()
    for pid, p in PRODUCTS.items():
        if p["cat"] != cat:
            continue
        s_count = stock(pid)
        label = f"{p['title']} — {p['price']}₽"
        if s_count == 0:
            label += " (Нет в наличии)"
        kb.button(text=label, callback_data=f"buy:{pid}")
    kb.button(text="⬅️ Назад", callback_data="home")
    kb.adjust(1)
    await c.message.edit_text(
        f"🛒 **{CATEGORIES[cat]}**\nВыберите нужный срок подписки:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_item(c: CallbackQuery):
    pid = c.data.split(":", 1)[1]
    p = PRODUCTS.get(pid)
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    
    if stock(pid) == 0:
        return await c.answer("К сожалению, ключи закончились. Скоро пополним!", show_alert=True)

    cursor = db.execute(
        "INSERT INTO orders(user_id, product_id, status) VALUES(?,?,?)",
        (c.from_user.id, pid, "pending")
    )
    db.commit()
    order_id = cursor.lastrowid

    text = (
        f"📦 <b>Товар:</b> {p['title']}\n"
        f"💰 <b>К оплате:</b> <code>{p['price']} ₽</code>\n\n"
        f"💳 <b>Реквизиты для оплаты по СБП:</b>\n"
        f"• Банк: <b>{SBP_BANK}</b>\n"
        f"• Телефон: <code>{SBP_PHONE}</code>\n"
        f"• Получатель: <b>{RECIPIENT_NAME}</b>\n\n"
        f"⚠️ <b>Обязательно</b> переводи точную сумму ({p['price']} ₽).\n"
        f"После перевода нажми кнопку ниже 👇"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Я оплатил(а)", callback_data=f"paid:{order_id}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="home")]
    ])

    await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await c.answer()


@router.callback_query(F.data.startswith("paid:"))
async def user_paid(c: CallbackQuery, bot: Bot):
    order_id = int(c.data.split(":", 1)[1])
    order = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, c.from_user.id)).fetchone()
    
    if not order or order["status"] != "pending":
        return await c.answer("Заказ не найден или уже обработан.", show_alert=True)

    p = PRODUCTS[order["product_id"]]
    user = c.from_user
    username_str = f"@{user.username}" if user.username else f"ID: {user.id}"

    if ADMIN_ID:
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить и выдать ключ", callback_data=f"adm_yes:{order_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no:{order_id}")
            ]
        ])
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Новая оплата по СБП!</b>\n\n"
            f"Покупатель: {username_str} (<code>{user.id}</code>)\n"
            f"Товар: {p['title']} ({p['price']} ₽)\n"
            f"Заказ ID: {order_id}",
            reply_markup=admin_kb,
            parse_mode="HTML"
        )

    await c.message.edit_text(
        "⏳ **Оплата проверяется!**\n\n"
        "Администратор получил уведомление. Как только платеж поступит, бот автоматически пришлет тебе ключ. Обычно это занимает пару минут.",
        parse_mode="HTML"
    )
    await c.answer()


# ---------------------------------------------------------------------------
# Администратор (Подтверждение выдачи)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm_yes:") | F.data.startswith("adm_no:"))
async def admin_decision(c: CallbackQuery, bot: Bot):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("Недостаточно прав", show_alert=True)

    action, order_id_str = c.data.split(":")
    order_id = int(order_id_str)

    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "pending":
        return await c.answer("Заказ уже обработан или не существует.", show_alert=True)

    pid = order["product_id"]
    user_id = order["user_id"]

    if action == "adm_yes":
        key_row = get_free_key(pid)
        if not key_row:
            return await c.answer("❌ Ошибка: Свободные ключи на этот товар закончились!", show_alert=True)

        db.execute("UPDATE keys SET status='sold', buyer_id=? WHERE id=?", (user_id, key_row["id"]))
        db.execute("UPDATE orders SET status='completed', key_id=? WHERE id=?", (key_row["id"], order_id))
        db.commit()

        reviews_btn = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Оставить отзыв", url=REVIEWS_URL)]
        ]) if REVIEWS_URL else None

        try:
            await bot.send_message(
                user_id,
                "✅ <b>Оплата подтверждена! Вот твой ключ:</b>\n\n"
                f"<code>{html.escape(key_row['key_text'])}</code>\n\n"
                "Нажми на ключ, чтобы скопировать. Спасибо за покупку в ASKU!",
                reply_markup=reviews_btn,
                parse_mode="HTML"
            )
        except Exception:
            pass

        await c.message.edit_text(f"{c.message.text}\n\n✅ <b>СТАТУС:</b> Подтверждено, ключ выдан.")
        await c.answer("Ключ успешно выдан покупателю!")

    else:
        db.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
        db.commit()
        try:
            await bot.send_message(
                user_id,
                "❌ К сожалению, администратор отклонил платеж (возможно, деньги не поступили или сумма неверная). Если это ошибка, напиши @askuSTOR."
            )
        except Exception:
            pass

        await c.message.edit_text(f"{c.message.text}\n\n❌ <b>СТАТУС:</b> Отклонено.")
        await c.answer("Заказ отклонен.")


# ---------------------------------------------------------------------------
# Команды админа для добавления ключей
# ---------------------------------------------------------------------------
@router.message(Command("addkeys"))
async def add_keys(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    lines = (m.text or "").split("\n")
    head = lines[0].split()
    if len(head) != 2 or head[1] not in PRODUCTS:
        ids = ", ".join(PRODUCTS.keys())
        return await m.answer(
            "Формат: первая строка «/addkeys ID_товара», дальше по одному ключу в строке.\n"
            f"Доступные ID: {ids}"
        )
    keys = [ln.strip() for ln in lines[1:] if ln.strip()]
    db.executemany(
        "INSERT INTO keys(product_id, key_text) VALUES(?,?)", [(head[1], k) for k in keys]
    )
    db.commit()
    await m.answer(f"Добавлено ключей: {len(keys)}. В наличии для {head[1]}: {stock(head[1])}")


@router.message(Command("stock"))
async def show_stock(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    text = "\n".join(f"{pid} ({p['title']}): {stock(pid)} шт." for pid, p in PRODUCTS.items())
    await m.answer(f"📦 <b>Остаток ключей:</b>\n\n{text}", parse_mode="HTML")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
