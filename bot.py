import asyncio
import csv
import io
import math
import re
from dataclasses import dataclass
from typing import Dict
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import load_settings


PAGE_SIZE = 10


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    description: str
    price: int


FALLBACK_PRODUCTS = [
    Product("pod_1", "POD система Aegis", "Компактная POD-система, 1100 mAh.", 2890),
    Product("salt_1", "Жидкость Salt Mango", "30 мл, крепость 20 мг.", 690),
    Product("coil_1", "Испаритель X2", "Сетчатый испаритель 0.8 Ω.", 390),
]


class Checkout(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_address = State()


settings = load_settings()
PRODUCTS: list[Product] = []


def apply_markup(base_price: float) -> int:
    if base_price <= 200:
        multiplier = 1.8
    elif base_price <= 250:
        multiplier = 1.5
    else:
        multiplier = 1.35
    return int(math.ceil(base_price * multiplier))


def _extract_numeric_price(raw_value: str) -> float | None:
    cleaned = raw_value.strip().replace("\xa0", " ")
    cleaned = cleaned.replace("₽", "").replace("руб.", "").replace("р.", "")
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    if not cleaned:
        return None

    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace("ё", "е")


def _pick_columns(headers: list[str]) -> tuple[int, int] | None:
    normalized = [_normalize_header(h) for h in headers]

    name_candidates = ["название", "товар", "наименование", "product", "name"]
    price_candidates = ["цена", "прайс", "стоимость", "price"]

    name_index = next((i for i, h in enumerate(normalized) if any(c in h for c in name_candidates)), None)
    price_index = next((i for i, h in enumerate(normalized) if any(c in h for c in price_candidates)), None)

    if name_index is not None and price_index is not None:
        return name_index, price_index

    if len(headers) >= 2:
        return 0, 1

    return None


def _google_sheet_to_csv_url(url: str) -> str:
    if "docs.google.com/spreadsheets" not in url:
        return url

    sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    if not sheet_id_match:
        return url

    parsed = urlparse(url)
    query_gid = parse_qs(parsed.query).get("gid", [None])[0]
    fragment_gid = parse_qs(parsed.fragment).get("gid", [None])[0]
    gid = query_gid or fragment_gid or "0"

    return f"https://docs.google.com/spreadsheets/d/{sheet_id_match.group(1)}/export?format=csv&gid={gid}"


def parse_price_products(csv_text: str) -> list[Product]:
    reader = csv.reader(io.StringIO(csv_text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []

    columns = _pick_columns(rows[0])
    data_rows = rows[1:] if columns and rows[0] else rows

    if columns is None:
        return []

    name_col, price_col = columns
    products: list[Product] = []

    for idx, row in enumerate(data_rows, start=1):
        if len(row) <= max(name_col, price_col):
            continue

        title = row[name_col].strip()
        base_price = _extract_numeric_price(row[price_col])
        if not title or base_price is None:
            continue

        products.append(
            Product(
                id=f"price_{idx}",
                title=title,
                description=f"Базовая цена: {int(round(base_price))} ₽",
                price=apply_markup(base_price),
            )
        )

    return products


def load_catalog_products() -> list[Product]:
    if not settings.catalog_url:
        return FALLBACK_PRODUCTS

    source_url = _google_sheet_to_csv_url(settings.catalog_url)

    try:
        with urlopen(source_url, timeout=30) as response:
            text = response.read().decode("utf-8-sig", errors="ignore")
        parsed = parse_price_products(text)
        if parsed:
            return parsed
    except Exception:
        pass

    return FALLBACK_PRODUCTS


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Каталог", callback_data="open_catalog:0")],
            [InlineKeyboardButton(text="🧺 Корзина", callback_data="open_cart")],
            [InlineKeyboardButton(text="ℹ️ О магазине", callback_data="about")],
        ]
    )


def catalog_keyboard(page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    rows = []
    for item in PRODUCTS[start:end]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{item.title[:45]} — {item.price} ₽",
                    callback_data=f"add:{item.id}:{page}",
                )
            ]
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"open_catalog:{page - 1}"))
    if end < len(PRODUCTS):
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"open_catalog:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
            [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
        ]
    )


def find_product(product_id: str) -> Product | None:
    return next((p for p in PRODUCTS if p.id == product_id), None)


def format_cart(cart: Dict[str, int]) -> str:
    if not cart:
        return "Ваша корзина пуста."

    lines = ["🧺 Ваша корзина:"]
    total = 0
    for pid, qty in cart.items():
        product = find_product(pid)
        if not product:
            continue
        subtotal = product.price * qty
        total += subtotal
        lines.append(f"• {product.title} × {qty} = {subtotal} ₽")

    lines.append(f"\nИтого: {total} ₽")
    return "\n".join(lines)


async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Это бот-магазин. Выберите действие:",
        reply_markup=main_menu(),
    )


async def open_catalog(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":", maxsplit=1)[1])
    page_total = max(1, math.ceil(len(PRODUCTS) / PAGE_SIZE))
    await callback.message.edit_text(
        f"Каталог (страница {page + 1}/{page_total}). Выберите товар:",
        reply_markup=catalog_keyboard(page),
    )
    await callback.answer()


async def add_to_cart(callback: CallbackQuery, state: FSMContext) -> None:
    _, product_id, page = callback.data.split(":", maxsplit=2)
    product = find_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    data = await state.get_data()
    cart = data.get("cart", {})
    cart[product.id] = cart.get(product.id, 0) + 1
    await state.update_data(cart=cart)

    await callback.answer("Добавлено в корзину ✅")
    await callback.message.edit_reply_markup(reply_markup=catalog_keyboard(int(page)))


async def open_cart(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart = data.get("cart", {})
    await callback.message.edit_text(format_cart(cart), reply_markup=cart_keyboard())
    await callback.answer()


async def clear_cart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(cart={})
    await callback.message.edit_text("Корзина очищена.", reply_markup=main_menu())
    await callback.answer()


async def about(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Каталог загружается из прайс-листа, а цены автоматически пересчитываются с наценкой.",
        reply_markup=main_menu(),
    )
    await callback.answer()


async def back_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    await callback.answer()


async def checkout_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("cart"):
        await callback.answer("Корзина пуста", show_alert=True)
        return

    await state.set_state(Checkout.waiting_name)
    await callback.message.answer("Введите ваше имя для заказа:")
    await callback.answer()


async def checkout_name(message: Message, state: FSMContext) -> None:
    await state.update_data(customer_name=message.text)
    await state.set_state(Checkout.waiting_phone)
    await message.answer("Введите телефон для связи:")


async def checkout_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(customer_phone=message.text)
    await state.set_state(Checkout.waiting_address)
    await message.answer("Введите адрес доставки (или самовывоза):")


async def checkout_address(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    cart = data.get("cart", {})

    await state.update_data(customer_address=message.text)
    summary = format_cart(cart)

    order_text = (
        "🧾 Новый заказ\n"
        f"Покупатель: {data.get('customer_name')}\n"
        f"Телефон: {data.get('customer_phone')}\n"
        f"Адрес: {message.text}\n\n"
        f"{summary}"
    )

    await bot.send_message(settings.admin_id, order_text)
    await message.answer("Спасибо! Заказ отправлен администратору ✅")
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())


async def main() -> None:
    global PRODUCTS
    PRODUCTS = load_catalog_products()

    bot = Bot(settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(on_start, CommandStart())

    dp.callback_query.register(open_catalog, F.data.startswith("open_catalog:"))
    dp.callback_query.register(open_cart, F.data == "open_cart")
    dp.callback_query.register(about, F.data == "about")
    dp.callback_query.register(back_main, F.data == "back_main")
    dp.callback_query.register(clear_cart, F.data == "clear_cart")
    dp.callback_query.register(checkout_start, F.data == "checkout")
    dp.callback_query.register(add_to_cart, F.data.startswith("add:"))

    dp.message.register(checkout_name, Checkout.waiting_name)
    dp.message.register(checkout_phone, Checkout.waiting_phone)
    dp.message.register(checkout_address, Checkout.waiting_address)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
