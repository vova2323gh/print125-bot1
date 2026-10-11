import asyncio
import os
import sqlite3
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8811593795"))
CARD = os.getenv("CARD", "ВКАЖИ_НОМЕР_КАРТКИ")
BW_PRICE = int(os.getenv("BW_PRICE", "4"))
COLOR_PRICE = int(os.getenv("COLOR_PRICE", "8"))

if not BOT_TOKEN:
    raise RuntimeError("Не задано BOT_TOKEN у файлі .env")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

DB = Path("orders.db")
conn = sqlite3.connect(DB)
conn.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    filename TEXT,
    file_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    kind TEXT NOT NULL,
    pages INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Нове'
)
""")
conn.commit()

# Антиспам: не більше 3 незавершених замовлень на користувача.
MAX_ACTIVE_ORDERS = 3
START_COOLDOWN = 5
start_cooldowns = {}


class Order(StatesGroup):
    file = State()
    kind = State()
    pages = State()


def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖨 Замовити друк", callback_data="new_order")],
        [InlineKeyboardButton(text="💰 Ціни", callback_data="prices")],
        [InlineKeyboardButton(text="📋 Мої замовлення", callback_data="my_orders")],
    ])


def kind_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚫ Ч/Б — 4 грн", callback_data="kind_bw"),
            InlineKeyboardButton(text="🌈 Кольоровий — 8 грн", callback_data="kind_color"),
        ]
    ])


def payment_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Я оплатив", callback_data=f"paid:{order_id}")]
    ])


def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Нові", callback_data="adm_new"), InlineKeyboardButton(text="🖨 В роботі", callback_data="adm_work")],
        [InlineKeyboardButton(text="✅ Виконані", callback_data="adm_done"), InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💰 Ціни", callback_data="adm_prices"), InlineKeyboardButton(text="📢 Розсилка", callback_data="adm_broadcast")]
    ])


def admin_order_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🖨 Друкую", callback_data=f"work:{order_id}"),
            InlineKeyboardButton(text="✅ Готово", callback_data=f"done:{order_id}"),
        ]
    ])


@dp.message(Command("start"))
async def start(message: Message):
    conn.execute("INSERT OR REPLACE INTO users(user_id, username, first_name) VALUES (?, ?, ?)", (message.from_user.id, message.from_user.username or "", message.from_user.first_name or ""))
    conn.commit()
    await message.answer(
        "👋 Привіт! Це бот замовлення друку в гуртожитку.\n\n"
        "Обери потрібну дію:",
        reply_markup=main_kb()
    )


@dp.callback_query(F.data == "prices")
async def prices(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        f"💰 Ціни:\n\n⚫ Ч/Б — {BW_PRICE} грн/стор.\n"
        f"🌈 Кольоровий — {COLOR_PRICE} грн/стор."
    )


@dp.callback_query(F.data == "new_order")
async def new_order(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    now = time.monotonic()
    last = start_cooldowns.get(uid, 0)
    if now - last < START_COOLDOWN:
        await call.answer("⏳ Не так швидко. Спробуй через кілька секунд.", show_alert=True)
        return

    active = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id=? AND status IN ('Нове','Оплачено','Друкую')",
        (uid,)
    ).fetchone()[0]
    if active >= MAX_ACTIVE_ORDERS:
        await call.answer(
            f"⛔ У тебе вже {MAX_ACTIVE_ORDERS} незавершені замовлення. Дочекайся виконання.",
            show_alert=True
        )
        return

    start_cooldowns[uid] = now
    await call.answer()
    await state.set_state(Order.file)
    await call.message.answer(
        "📎 Надішли файл для друку.\n"
        "Можна PDF, Word, фото або інший документ."
    )


@dp.message(Order.file, F.document)
async def receive_document(message: Message, state: FSMContext):
    await state.update_data(
        file_id=message.document.file_id,
        file_type="document",
        filename=message.document.file_name or "document"
    )
    await state.set_state(Order.kind)
    await message.answer("Обери тип друку:", reply_markup=kind_kb())


@dp.message(Order.file, F.photo)
async def receive_photo(message: Message, state: FSMContext):
    await state.update_data(
        file_id=message.photo[-1].file_id,
        file_type="photo",
        filename="photo.jpg"
    )
    await state.set_state(Order.kind)
    await message.answer("Обери тип друку:", reply_markup=kind_kb())


@dp.callback_query(Order.kind, F.data.in_({"kind_bw", "kind_color"}))
async def choose_kind(call: CallbackQuery, state: FSMContext):
    await call.answer()
    kind = "Ч/Б" if call.data == "kind_bw" else "Кольоровий"
    await state.update_data(kind=kind)
    await state.set_state(Order.pages)
    await call.message.answer("📄 Напиши кількість сторінок цифрою:")


@dp.message(Order.pages, F.text)
async def receive_pages(message: Message, state: FSMContext):
    try:
        pages = int(message.text.strip())
        if pages <= 0 or pages > 10000:
            raise ValueError
    except ValueError:
        await message.answer("❗ Введи нормальну кількість сторінок, наприклад: 10")
        return

    data = await state.get_data()

    # Повторна перевірка перед створенням замовлення: захист від обходу антиспаму.
    active = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id=? AND status IN ('Нове','Оплачено','Друкую')",
        (message.from_user.id,)
    ).fetchone()[0]
    if active >= MAX_ACTIVE_ORDERS:
        await state.clear()
        await message.answer(
            f"⛔ У тебе вже {MAX_ACTIVE_ORDERS} незавершені замовлення. Дочекайся виконання."
        )
        return

    price = BW_PRICE if data["kind"] == "Ч/Б" else COLOR_PRICE
    amount = pages * price

    cur = conn.execute(
        """INSERT INTO orders
        (user_id, username, filename, file_id, file_type, kind, pages, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message.from_user.id,
            message.from_user.username or "",
            data["filename"],
            data["file_id"],
            data["file_type"],
            data["kind"],
            pages,
            amount,
        ),
    )
    conn.commit()
    order_id = cur.lastrowid

    await state.clear()

    await message.answer(
        f"🧾 Замовлення №{order_id}\n\n"
        f"Тип: {data['kind']}\n"
        f"Сторінок: {pages}\n"
        f"Сума: {amount} грн\n\n"
        f"💳 Оплати на картку:\n`{CARD}`\n\n"
        f"Після оплати натисни кнопку нижче.",
        reply_markup=payment_kb(order_id),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("paid:"))
async def paid(call: CallbackQuery):
    order_id = int(call.data.split(":")[1])

    row = conn.execute(
        "SELECT user_id, username, filename, file_id, file_type, kind, pages, amount, status "
        "FROM orders WHERE id=?", (order_id,)
    ).fetchone()
    if not row:
        await call.answer("Замовлення не знайдено.", show_alert=True)
        return

    user_id, username, filename, file_id, file_type, kind, pages, amount, status = row
    if call.from_user.id != user_id:
        await call.answer("Це не ваше замовлення.", show_alert=True)
        return

    # Головний захист: повторне натискання не створює нове сповіщення адміну.
    if status != "Нове":
        await call.answer("Оплату вже передано адміністратору ✅", show_alert=True)
        return

    cur = conn.execute(
        "UPDATE orders SET status='Оплачено' WHERE id=? AND status='Нове'",
        (order_id,)
    )
    conn.commit()
    if cur.rowcount != 1:
        await call.answer("Оплату вже передано адміністратору ✅", show_alert=True)
        return

    # Прибираємо кнопку, щоб її не можна було натискати знову.
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await call.answer("Оплату передано адміністратору ✅")
    caption = (
        f"🔔 НОВЕ ОПЛАЧЕНЕ ЗАМОВЛЕННЯ №{order_id}\n\n"
        f"👤 @{username or 'без username'}\n"
        f"🆔 ID: {user_id}\n"
        f"📄 Файл: {filename}\n"
        f"🖨 Тип: {kind}\n"
        f"📑 Сторінок: {pages}\n"
        f"💰 Сума: {amount} грн"
    )

    await bot.send_message(ADMIN_ID, caption, reply_markup=admin_order_kb(order_id))
    if file_type == "photo":
        await bot.send_photo(ADMIN_ID, file_id)
    else:
        await bot.send_document(ADMIN_ID, file_id)

    await call.message.answer(
        f"✅ Замовлення №{order_id} передано адміну.\n"
        "Коли воно буде готове, я повідомлю тебе."
    )


@dp.callback_query(F.data.startswith("work:"))
async def set_work(call: CallbackQuery):
    await call.answer("Статус: друкую 🖨")
    order_id = int(call.data.split(":")[1])
    row = conn.execute("SELECT user_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return
    conn.execute("UPDATE orders SET status='Друкую' WHERE id=?", (order_id,))
    conn.commit()
    await bot.send_message(row[0], f"🖨 Замовлення №{order_id} зараз друкується.")


@dp.callback_query(F.data.startswith("done:"))
async def set_done(call: CallbackQuery):
    await call.answer("Статус: готово ✅")
    order_id = int(call.data.split(":")[1])
    row = conn.execute("SELECT user_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return
    conn.execute("UPDATE orders SET status='Готово' WHERE id=?", (order_id,))
    conn.commit()
    await bot.send_message(row[0], f"✅ Замовлення №{order_id} готове! Можеш забирати.")


@dp.callback_query(F.data == "my_orders")
async def my_orders(call: CallbackQuery):
    await call.answer()
    rows = conn.execute(
        "SELECT id, kind, pages, amount, status FROM orders "
        "WHERE user_id=? ORDER BY id DESC LIMIT 10",
        (call.from_user.id,)
    ).fetchall()

    if not rows:
        await call.message.answer("У тебе ще немає замовлень.")
        return

    text = "📋 Твої останні замовлення:\n\n"
    for oid, kind, pages, amount, status in rows:
        text += f"№{oid} — {kind}, {pages} стор., {amount} грн — {status}\n"
    await call.message.answer(text)


@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Немає доступу.")
        return
    await message.answer("🛠 Панель адміністратора:", reply_markup=admin_menu_kb())


@dp.callback_query(F.data.startswith("adm_"))
async def admin_actions(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔ Немає доступу.", show_alert=True)
        return
    await call.answer()
    a=call.data
    if a=="adm_new":
        rows=conn.execute("SELECT id, username, filename, kind, pages, amount, status FROM orders WHERE status IN ('Нове','Оплачено') ORDER BY id DESC LIMIT 30").fetchall()
        if not rows: await call.message.answer("📭 Нових замовлень немає.")
        for oid,u,f,k,pg,amt,st in rows:
            await call.message.answer(f"№{oid} | {st}\n👤 @{u or 'без username'}\n📄 {f}\n🖨 {k}, {pg} стор.\n💰 {amt} грн", reply_markup=admin_order_kb(oid))
    elif a=="adm_work":
        rows=conn.execute("SELECT id, username, filename, kind, pages, amount FROM orders WHERE status='Друкую' ORDER BY id DESC LIMIT 30").fetchall()
        if not rows: await call.message.answer("📭 Замовлень у роботі немає.")
        for oid,u,f,k,pg,amt in rows:
            await call.message.answer(f"№{oid} | 🖨 Друкую\n👤 @{u or 'без username'}\n📄 {f}\n🖨 {k}, {pg} стор.\n💰 {amt} грн", reply_markup=admin_order_kb(oid))
    elif a=="adm_done":
        rows=conn.execute("SELECT id, username, filename, kind, pages, amount FROM orders WHERE status='Готово' ORDER BY id DESC LIMIT 30").fetchall()
        if not rows: await call.message.answer("📭 Виконаних замовлень ще немає.")
        else:
            text="✅ Останні виконані:\n\n"+"\n".join(f"№{oid} — @{u or 'без username'} — {k}, {pg} стор. — {amt} грн" for oid,u,f,k,pg,amt in rows)
            await call.message.answer(text)
    elif a=="adm_stats":
        total=conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        users=conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        paid_count,turn=conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE status IN ('Оплачено','Друкую','Готово')").fetchone()
        done_count,done_sum=conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE status='Готово'").fetchone()
        pages=conn.execute("SELECT COALESCE(SUM(pages),0) FROM orders WHERE status IN ('Оплачено','Друкую','Готово')").fetchone()[0]
        await call.message.answer(f"📊 Статистика\n\n👥 Користувачів: {users}\n🧾 Замовлень: {total}\n💳 Оплачених: {paid_count}\n📄 Сторінок: {pages}\n💰 Оборот: {turn} грн\n✅ Виконано: {done_count}\n💵 Виконано на: {done_sum} грн")
    elif a=="adm_prices":
        await call.message.answer(f"💰 Ціни зараз:\n⚫ Ч/Б — {BW_PRICE} грн/стор.\n🌈 Кольоровий — {COLOR_PRICE} грн/стор.\n\nЗміна:\n/setbw 4\n/setcolor 8")
    elif a=="adm_broadcast":
        await state.set_state(AdminState.broadcast)
        await call.message.answer("📢 Напиши текст розсилки для всіх користувачів бота.")


@dp.message(Command("setbw"))
async def set_bw(message: Message):
    global BW_PRICE
    if message.from_user.id != ADMIN_ID: return
    p=message.text.split()
    if len(p)!=2 or not p[1].isdigit() or int(p[1])<=0:
        await message.answer("Приклад: /setbw 4"); return
    BW_PRICE=int(p[1]); await message.answer(f"✅ Ч/Б тепер {BW_PRICE} грн/стор.")


@dp.message(Command("setcolor"))
async def set_color(message: Message):
    global COLOR_PRICE
    if message.from_user.id != ADMIN_ID: return
    p=message.text.split()
    if len(p)!=2 or not p[1].isdigit() or int(p[1])<=0:
        await message.answer("Приклад: /setcolor 8"); return
    COLOR_PRICE=int(p[1]); await message.answer(f"✅ Кольоровий друк тепер {COLOR_PRICE} грн/стор.")


@dp.message(AdminState.broadcast, F.text)
async def broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear(); return
    users=[x[0] for x in conn.execute("SELECT user_id FROM users").fetchall()]
    sent=failed=0
    for uid in users:
        try:
            await bot.send_message(uid, "📢 Повідомлення від друку:\n\n"+message.text); sent+=1
        except Exception: failed+=1
    await state.clear()
    await message.answer(f"✅ Розсилку завершено.\nНадіслано: {sent}\nНе доставлено: {failed}")

async def main():
    print("Бот запущено.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
