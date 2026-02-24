import asyncio
from dataclasses import dataclass
from typing import Dict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import load_settings


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    description: str
    price: int


PRODUCTS = [
    Product("pod_1", "POD система Aegis", "Компактная POD-система, 1100 mAh.", 2890),
    Product("salt_1", "Жидкость Salt Mango", "30 мл, крепость 20 мг.", 690),
    Product("coil_1", "Испаритель X2", "Сетчатый испаритель 0.8 Ω.", 390),
]


class Checkout(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_address = State()


settings = load_settings()


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Каталог", callback_data="open_catalog")],
            [InlineKeyboardButton(text="🧺 Корзина", callback_data="open_cart")],
            [InlineKeyboardButton(text="ℹ️ О магазине", callback_data="about")],
        ]
    )


def catalog_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for item in PRODUCTS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{item.title} — {item.price} ₽",
                    callback_data=f"add:{item.id}",
                )
            ]
        )
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
    await callback.message.edit_text(
        "Выберите товар для добавления в корзину:",
        reply_markup=catalog_keyboard(),
    )
    await callback.answer()


async def add_to_cart(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = callback.data.split(":", maxsplit=1)[1]
    product = find_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    data = await state.get_data()
    cart = data.get("cart", {})
    cart[product.id] = cart.get(product.id, 0) + 1
    await state.update_data(cart=cart)

    await callback.answer("Добавлено в корзину ✅")


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
        "Мы продаём товары для вейпа. Заказы обрабатывает администратор вручную после оплаты.",
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
    bot = Bot(settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(on_start, CommandStart())

    dp.callback_query.register(open_catalog, F.data == "open_catalog")
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
