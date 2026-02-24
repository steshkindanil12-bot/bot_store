 import asyncio
-import csv
-import io
 import math
-import re
-from dataclasses import dataclass
+import sqlite3
+from pathlib import Path
 from typing import Dict
-from urllib.parse import parse_qs, urlparse
-from urllib.request import urlopen
 
 from aiogram import Bot, Dispatcher, F
-from aiogram.filters import CommandStart
+from aiogram.filters import Command, CommandStart
 from aiogram.fsm.context import FSMContext
 from aiogram.fsm.state import State, StatesGroup
 from aiogram.fsm.storage.memory import MemoryStorage
 from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
 
 from config import load_settings
+from products import products as products_data
 
 
 PAGE_SIZE = 10
-
-
-@dataclass(frozen=True)
-class Product:
-    id: str
-    title: str
-    description: str
-    price: int
-
-
-FALLBACK_PRODUCTS = [
-    Product("pod_1", "POD система Aegis", "Компактная POD-система, 1100 mAh.", 2890),
-    Product("salt_1", "Жидкость Salt Mango", "30 мл, крепость 20 мг.", 690),
-    Product("coil_1", "Испаритель X2", "Сетчатый испаритель 0.8 Ω.", 390),
-]
+MARKER_TOKENS = {"HARD", "MEDIUM", "LIGHT", "V2"}
+DB_PATH = Path("bot_store.db")
 
 
 class Checkout(StatesGroup):
     waiting_name = State()
     waiting_phone = State()
     waiting_address = State()
 
 
 settings = load_settings()
-PRODUCTS: list[Product] = []
-
-
-def apply_markup(base_price: float) -> int:
-    if base_price <= 200:
-        multiplier = 1.8
-    elif base_price <= 250:
-        multiplier = 1.5
-    else:
-        multiplier = 1.35
-    return int(math.ceil(base_price * multiplier))
-
-
-def _extract_numeric_price(raw_value: str) -> float | None:
-    cleaned = raw_value.strip().replace("\xa0", " ")
-    cleaned = cleaned.replace("₽", "").replace("руб.", "").replace("р.", "")
-    cleaned = cleaned.replace(" ", "").replace(",", ".")
-    if not cleaned:
-        return None
-
-    match = re.search(r"\d+(?:\.\d+)?", cleaned)
-    if not match:
-        return None
-
-    try:
-        return float(match.group(0))
-    except ValueError:
-        return None
-
-
-def _normalize_header(value: str) -> str:
-    return value.strip().lower().replace("ё", "е")
-
-
-def _pick_columns(headers: list[str]) -> tuple[int, int] | None:
-    normalized = [_normalize_header(h) for h in headers]
-
-    name_candidates = ["название", "товар", "наименование", "product", "name"]
-    price_candidates = ["цена", "прайс", "стоимость", "price"]
-
-    name_index = next((i for i, h in enumerate(normalized) if any(c in h for c in name_candidates)), None)
-    price_index = next((i for i, h in enumerate(normalized) if any(c in h for c in price_candidates)), None)
 
-    if name_index is not None and price_index is not None:
-        return name_index, price_index
 
-    if len(headers) >= 2:
-        return 0, 1
+def round_to_5(price: int) -> int:
+    return int(round(price / 5) * 5)
 
-    return None
 
+def split_line_and_flavor(name: str) -> tuple[str, str]:
+    tokens = name.split()
+    marker_indexes = [idx for idx, token in enumerate(tokens) if token.upper() in MARKER_TOKENS]
 
-def _google_sheet_to_csv_url(url: str) -> str:
-    if "docs.google.com/spreadsheets" not in url:
-        return url
+    if marker_indexes:
+        marker_index = marker_indexes[-1]
+        line = " ".join(tokens[: marker_index + 1]).strip()
+        flavor = " ".join(tokens[marker_index + 1 :]).strip()
+    else:
+        line = tokens[0].strip() if tokens else name.strip()
+        flavor = " ".join(tokens[1:]).strip()
 
-    sheet_id_match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
-    if not sheet_id_match:
-        return url
+    return line or name.strip(), flavor or "Классический"
 
-    parsed = urlparse(url)
-    query_gid = parse_qs(parsed.query).get("gid", [None])[0]
-    fragment_gid = parse_qs(parsed.fragment).get("gid", [None])[0]
-    gid = query_gid or fragment_gid or "0"
 
-    return f"https://docs.google.com/spreadsheets/d/{sheet_id_match.group(1)}/export?format=csv&gid={gid}"
+def db_connect() -> sqlite3.Connection:
+    conn = sqlite3.connect(DB_PATH)
+    conn.row_factory = sqlite3.Row
+    return conn
 
 
-def parse_price_products(csv_text: str) -> list[Product]:
-    reader = csv.reader(io.StringIO(csv_text))
-    rows = [row for row in reader if any(cell.strip() for cell in row)]
-    if not rows:
-        return []
+def init_db() -> None:
+    with db_connect() as conn:
+        conn.execute(
+            """
+            CREATE TABLE IF NOT EXISTS users (
+                user_id INTEGER PRIMARY KEY,
+                first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
+            )
+            """
+        )
+        conn.execute(
+            """
+            CREATE TABLE IF NOT EXISTS sections (
+                id INTEGER PRIMARY KEY AUTOINCREMENT,
+                name TEXT NOT NULL UNIQUE
+            )
+            """
+        )
+        conn.execute(
+            """
+            CREATE TABLE IF NOT EXISTS subsections (
+                id INTEGER PRIMARY KEY AUTOINCREMENT,
+                section_id INTEGER NOT NULL,
+                name TEXT NOT NULL,
+                FOREIGN KEY(section_id) REFERENCES sections(id) ON DELETE CASCADE
+            )
+            """
+        )
+        conn.execute(
+            """
+            CREATE TABLE IF NOT EXISTS products (
+                id INTEGER PRIMARY KEY AUTOINCREMENT,
+                subsection_id INTEGER NOT NULL,
+                name TEXT NOT NULL,
+                price INTEGER NOT NULL,
+                FOREIGN KEY(subsection_id) REFERENCES subsections(id) ON DELETE CASCADE
+            )
+            """
+        )
+        conn.execute("PRAGMA foreign_keys=ON")
 
-    columns = _pick_columns(rows[0])
-    data_rows = rows[1:] if columns and rows[0] else rows
+        section_count = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
+        if section_count == 0:
+            seed_catalog(conn)
 
-    if columns is None:
-        return []
 
-    name_col, price_col = columns
-    products: list[Product] = []
+def seed_catalog(conn: sqlite3.Connection) -> None:
+    conn.execute("INSERT INTO sections(name) VALUES (?)", ("Жидкости",))
+    section_id = conn.execute("SELECT id FROM sections WHERE name = ?", ("Жидкости",)).fetchone()[0]
 
-    for idx, row in enumerate(data_rows, start=1):
-        if len(row) <= max(name_col, price_col):
-            continue
+    grouped: dict[str, list[tuple[str, int]]] = {}
+    for item in products_data:
+        line_name, flavor_name = split_line_and_flavor(item["name"])
+        grouped.setdefault(line_name, []).append((flavor_name, round_to_5(int(item["price"]))))
 
-        title = row[name_col].strip()
-        base_price = _extract_numeric_price(row[price_col])
-        if not title or base_price is None:
-            continue
-
-        products.append(
-            Product(
-                id=f"price_{idx}",
-                title=title,
-                description=f"Базовая цена: {int(round(base_price))} ₽",
-                price=apply_markup(base_price),
-            )
+    for line_name, flavors in grouped.items():
+        conn.execute(
+            "INSERT INTO subsections(section_id, name) VALUES (?, ?)",
+            (section_id, line_name),
+        )
+        subsection_id = conn.execute(
+            "SELECT id FROM subsections WHERE section_id = ? AND name = ?",
+            (section_id, line_name),
+        ).fetchone()[0]
+
+        conn.executemany(
+            "INSERT INTO products(subsection_id, name, price) VALUES (?, ?, ?)",
+            [(subsection_id, flavor, price) for flavor, price in flavors],
         )
 
-    return products
-
-
-def load_catalog_products() -> list[Product]:
-    if not settings.catalog_url:
-        return FALLBACK_PRODUCTS
 
-    source_url = _google_sheet_to_csv_url(settings.catalog_url)
+def is_admin(user_id: int) -> bool:
+    return user_id == settings.admin_id
 
-    try:
-        with urlopen(source_url, timeout=30) as response:
-            text = response.read().decode("utf-8-sig", errors="ignore")
-        parsed = parse_price_products(text)
-        if parsed:
-            return parsed
-    except Exception:
-        pass
 
-    return FALLBACK_PRODUCTS
+def register_user(user_id: int) -> None:
+    with db_connect() as conn:
+        conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
 
 
 def main_menu() -> InlineKeyboardMarkup:
     return InlineKeyboardMarkup(
         inline_keyboard=[
             [InlineKeyboardButton(text="🛍 Каталог", callback_data="open_catalog:0")],
             [InlineKeyboardButton(text="🧺 Корзина", callback_data="open_cart")],
             [InlineKeyboardButton(text="ℹ️ О магазине", callback_data="about")],
         ]
     )
 
 
-def catalog_keyboard(page: int) -> InlineKeyboardMarkup:
+def sections_keyboard(page: int) -> InlineKeyboardMarkup:
+    with db_connect() as conn:
+        sections = conn.execute("SELECT id, name FROM sections ORDER BY id").fetchall()
+
     start = page * PAGE_SIZE
     end = start + PAGE_SIZE
-    rows = []
-    for item in PRODUCTS[start:end]:
-        rows.append(
-            [
-                InlineKeyboardButton(
-                    text=f"{item.title[:45]} — {item.price} ₽",
-                    callback_data=f"add:{item.id}:{page}",
-                )
-            ]
-        )
+    rows = [
+        [InlineKeyboardButton(text=row["name"], callback_data=f"open_section:{row['id']}:0")]
+        for row in sections[start:end]
+    ]
 
-    nav_row = []
+    nav = []
     if page > 0:
-        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"open_catalog:{page - 1}"))
-    if end < len(PRODUCTS):
-        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"open_catalog:{page + 1}"))
-    if nav_row:
-        rows.append(nav_row)
+        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"open_catalog:{page - 1}"))
+    if end < len(sections):
+        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"open_catalog:{page + 1}"))
+    if nav:
+        rows.append(nav)
 
     rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
     return InlineKeyboardMarkup(inline_keyboard=rows)
 
 
-def cart_keyboard() -> InlineKeyboardMarkup:
-    return InlineKeyboardMarkup(
-        inline_keyboard=[
-            [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
-            [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
-            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
+def subsections_keyboard(section_id: int, page: int) -> InlineKeyboardMarkup:
+    with db_connect() as conn:
+        subs = conn.execute(
+            "SELECT id, name FROM subsections WHERE section_id = ? ORDER BY id",
+            (section_id,),
+        ).fetchall()
+
+    start = page * PAGE_SIZE
+    end = start + PAGE_SIZE
+    rows = [
+        [InlineKeyboardButton(text=row["name"][:55], callback_data=f"open_subsection:{row['id']}:0")]
+        for row in subs[start:end]
+    ]
+
+    nav = []
+    if page > 0:
+        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"open_section:{section_id}:{page - 1}"))
+    if end < len(subs):
+        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"open_section:{section_id}:{page + 1}"))
+    if nav:
+        rows.append(nav)
+
+    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="open_catalog:0")])
+    return InlineKeyboardMarkup(inline_keyboard=rows)
+
+
+def products_keyboard(subsection_id: int, page: int) -> InlineKeyboardMarkup:
+    with db_connect() as conn:
+        items = conn.execute(
+            "SELECT id, name, price FROM products WHERE subsection_id = ? ORDER BY id",
+            (subsection_id,),
+        ).fetchall()
+
+    start = page * PAGE_SIZE
+    end = start + PAGE_SIZE
+    rows = [
+        [
+            InlineKeyboardButton(
+                text=f"{row['name'][:40]} — {row['price']} ₽",
+                callback_data=f"add:{row['id']}:{subsection_id}:{page}",
+            )
         ]
-    )
+        for row in items[start:end]
+    ]
 
+    nav = []
+    if page > 0:
+        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"open_subsection:{subsection_id}:{page - 1}"))
+    if end < len(items):
+        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"open_subsection:{subsection_id}:{page + 1}"))
+    if nav:
+        rows.append(nav)
+
+    with db_connect() as conn:
+        section_id = conn.execute(
+            "SELECT section_id FROM subsections WHERE id = ?",
+            (subsection_id,),
+        ).fetchone()[0]
+
+    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"open_section:{section_id}:0")])
+    return InlineKeyboardMarkup(inline_keyboard=rows)
 
-def find_product(product_id: str) -> Product | None:
-    return next((p for p in PRODUCTS if p.id == product_id), None)
+
+def find_product(product_id: int) -> sqlite3.Row | None:
+    with db_connect() as conn:
+        return conn.execute("SELECT id, name, price FROM products WHERE id = ?", (product_id,)).fetchone()
 
 
 def format_cart(cart: Dict[str, int]) -> str:
     if not cart:
         return "Ваша корзина пуста."
 
     lines = ["🧺 Ваша корзина:"]
     total = 0
-    for pid, qty in cart.items():
-        product = find_product(pid)
-        if not product:
-            continue
-        subtotal = product.price * qty
-        total += subtotal
-        lines.append(f"• {product.title} × {qty} = {subtotal} ₽")
+
+    with db_connect() as conn:
+        for pid, qty in cart.items():
+            row = conn.execute("SELECT name, price FROM products WHERE id = ?", (int(pid),)).fetchone()
+            if not row:
+                continue
+            subtotal = row["price"] * qty
+            total += subtotal
+            lines.append(f"• {row['name']} × {qty} = {subtotal} ₽")
 
     lines.append(f"\nИтого: {total} ₽")
     return "\n".join(lines)
 
 
 async def on_start(message: Message, state: FSMContext) -> None:
     await state.clear()
-    await message.answer(
-        "Привет! Это бот-магазин. Выберите действие:",
-        reply_markup=main_menu(),
-    )
+    register_user(message.from_user.id)
+    await message.answer("Привет! Это бот-магазин. Выберите действие:", reply_markup=main_menu())
 
 
 async def open_catalog(callback: CallbackQuery) -> None:
     page = int(callback.data.split(":", maxsplit=1)[1])
-    page_total = max(1, math.ceil(len(PRODUCTS) / PAGE_SIZE))
+    with db_connect() as conn:
+        total = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
+    page_total = max(1, math.ceil(total / PAGE_SIZE))
+
+    await callback.message.edit_text(
+        f"Каталог (страница {page + 1}/{page_total}). Выберите раздел:",
+        reply_markup=sections_keyboard(page),
+    )
+    await callback.answer()
+
+
+async def open_section(callback: CallbackQuery) -> None:
+    _, section_id_raw, page_raw = callback.data.split(":", maxsplit=2)
+    section_id = int(section_id_raw)
+    page = int(page_raw)
+
+    with db_connect() as conn:
+        section = conn.execute("SELECT name FROM sections WHERE id = ?", (section_id,)).fetchone()
+        total = conn.execute("SELECT COUNT(*) FROM subsections WHERE section_id = ?", (section_id,)).fetchone()[0]
+
+    if not section:
+        await callback.answer("Раздел не найден", show_alert=True)
+        return
+
+    page_total = max(1, math.ceil(total / PAGE_SIZE))
     await callback.message.edit_text(
-        f"Каталог (страница {page + 1}/{page_total}). Выберите товар:",
-        reply_markup=catalog_keyboard(page),
+        f"{section['name']} (страница {page + 1}/{page_total}). Выберите подраздел:",
+        reply_markup=subsections_keyboard(section_id, page),
+    )
+    await callback.answer()
+
+
+async def open_subsection(callback: CallbackQuery) -> None:
+    _, subsection_id_raw, page_raw = callback.data.split(":", maxsplit=2)
+    subsection_id = int(subsection_id_raw)
+    page = int(page_raw)
+
+    with db_connect() as conn:
+        subsection = conn.execute("SELECT name FROM subsections WHERE id = ?", (subsection_id,)).fetchone()
+        total = conn.execute("SELECT COUNT(*) FROM products WHERE subsection_id = ?", (subsection_id,)).fetchone()[0]
+
+    if not subsection:
+        await callback.answer("Подраздел не найден", show_alert=True)
+        return
+
+    page_total = max(1, math.ceil(total / PAGE_SIZE))
+    await callback.message.edit_text(
+        f"{subsection['name']}\nВкусы (страница {page + 1}/{page_total}):",
+        reply_markup=products_keyboard(subsection_id, page),
     )
     await callback.answer()
 
 
 async def add_to_cart(callback: CallbackQuery, state: FSMContext) -> None:
-    _, product_id, page = callback.data.split(":", maxsplit=2)
+    _, product_id_raw, subsection_id_raw, page_raw = callback.data.split(":", maxsplit=3)
+    product_id = int(product_id_raw)
+    subsection_id = int(subsection_id_raw)
+    page = int(page_raw)
+
     product = find_product(product_id)
     if not product:
         await callback.answer("Товар не найден", show_alert=True)
         return
 
     data = await state.get_data()
     cart = data.get("cart", {})
-    cart[product.id] = cart.get(product.id, 0) + 1
+    key = str(product_id)
+    cart[key] = cart.get(key, 0) + 1
     await state.update_data(cart=cart)
 
     await callback.answer("Добавлено в корзину ✅")
-    await callback.message.edit_reply_markup(reply_markup=catalog_keyboard(int(page)))
+    await callback.message.edit_reply_markup(reply_markup=products_keyboard(subsection_id, page))
 
 
 async def open_cart(callback: CallbackQuery, state: FSMContext) -> None:
     data = await state.get_data()
     cart = data.get("cart", {})
     await callback.message.edit_text(format_cart(cart), reply_markup=cart_keyboard())
     await callback.answer()
 
 
+def cart_keyboard() -> InlineKeyboardMarkup:
+    return InlineKeyboardMarkup(
+        inline_keyboard=[
+            [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
+            [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
+            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
+        ]
+    )
+
+
 async def clear_cart(callback: CallbackQuery, state: FSMContext) -> None:
     await state.update_data(cart={})
     await callback.message.edit_text("Корзина очищена.", reply_markup=main_menu())
     await callback.answer()
 
 
 async def about(callback: CallbackQuery) -> None:
     await callback.message.edit_text(
-        "Каталог загружается из прайс-листа, а цены автоматически пересчитываются с наценкой.",
+        "Мы предлагаем большой выбор качественных жидкостей и аксессуаров для вейпа.\n"
+        "Только проверенные бренды, актуальные вкусы и быстрая доставка.\n"
+        "Постоянные акции и скидки для наших клиентов.",
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
 
-    await state.update_data(customer_address=message.text)
     summary = format_cart(cart)
-
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
 
 
+ADMIN_HELP = (
+    "Команды администратора:\n"
+    "/add_section <название>\n"
+    "/del_section <section_id>\n"
+    "/add_subsection <section_id> | <название>\n"
+    "/del_subsection <subsection_id>\n"
+    "/add_product <subsection_id> | <название> | <цена>\n"
+    "/del_product <product_id>\n"
+    "/users_count\n"
+    "/broadcast <текст>"
+)
+
+
+async def admin_help(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    await message.answer(ADMIN_HELP)
+
+
+async def add_section_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    name = message.text.replace("/add_section", "", 1).strip()
+    if not name:
+        await message.answer("Формат: /add_section <название>")
+        return
+    with db_connect() as conn:
+        conn.execute("INSERT INTO sections(name) VALUES (?)", (name,))
+    await message.answer("Раздел добавлен ✅")
+
+
+async def del_section_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    try:
+        section_id = int(message.text.replace("/del_section", "", 1).strip())
+    except ValueError:
+        await message.answer("Формат: /del_section <section_id>")
+        return
+    with db_connect() as conn:
+        conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
+    await message.answer("Раздел удалён ✅")
+
+
+async def add_subsection_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    payload = message.text.replace("/add_subsection", "", 1).strip()
+    if "|" not in payload:
+        await message.answer("Формат: /add_subsection <section_id> | <название>")
+        return
+    section_raw, name = [part.strip() for part in payload.split("|", maxsplit=1)]
+    try:
+        section_id = int(section_raw)
+    except ValueError:
+        await message.answer("section_id должен быть числом")
+        return
+    with db_connect() as conn:
+        conn.execute("INSERT INTO subsections(section_id, name) VALUES (?, ?)", (section_id, name))
+    await message.answer("Подраздел добавлен ✅")
+
+
+async def del_subsection_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    try:
+        subsection_id = int(message.text.replace("/del_subsection", "", 1).strip())
+    except ValueError:
+        await message.answer("Формат: /del_subsection <subsection_id>")
+        return
+    with db_connect() as conn:
+        conn.execute("DELETE FROM subsections WHERE id = ?", (subsection_id,))
+    await message.answer("Подраздел удалён ✅")
+
+
+async def add_product_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    payload = message.text.replace("/add_product", "", 1).strip()
+    parts = [part.strip() for part in payload.split("|", maxsplit=2)]
+    if len(parts) != 3:
+        await message.answer("Формат: /add_product <subsection_id> | <название> | <цена>")
+        return
+    subsection_raw, name, price_raw = parts
+    try:
+        subsection_id = int(subsection_raw)
+        price = round_to_5(int(price_raw))
+    except ValueError:
+        await message.answer("subsection_id и цена должны быть числами")
+        return
+    with db_connect() as conn:
+        conn.execute(
+            "INSERT INTO products(subsection_id, name, price) VALUES (?, ?, ?)",
+            (subsection_id, name, price),
+        )
+    await message.answer("Товар добавлен ✅")
+
+
+async def del_product_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    try:
+        product_id = int(message.text.replace("/del_product", "", 1).strip())
+    except ValueError:
+        await message.answer("Формат: /del_product <product_id>")
+        return
+    with db_connect() as conn:
+        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
+    await message.answer("Товар удалён ✅")
+
+
+async def users_count_cmd(message: Message) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    with db_connect() as conn:
+        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
+    await message.answer(f"Пользователей, использовавших бота: {count}")
+
+
+async def broadcast_cmd(message: Message, bot: Bot) -> None:
+    if not is_admin(message.from_user.id):
+        return
+    text = message.text.replace("/broadcast", "", 1).strip()
+    if not text:
+        await message.answer("Формат: /broadcast <текст>")
+        return
+
+    with db_connect() as conn:
+        user_ids = [row[0] for row in conn.execute("SELECT user_id FROM users").fetchall()]
+
+    sent = 0
+    for user_id in user_ids:
+        try:
+            await bot.send_message(user_id, text)
+            sent += 1
+        except Exception:
+            continue
+
+    await message.answer(f"Рассылка завершена. Отправлено: {sent}")
+
+
 async def main() -> None:
-    global PRODUCTS
-    PRODUCTS = load_catalog_products()
+    init_db()
 
     bot = Bot(settings.bot_token)
     dp = Dispatcher(storage=MemoryStorage())
 
     dp.message.register(on_start, CommandStart())
+    dp.message.register(admin_help, Command("admin"))
+    dp.message.register(add_section_cmd, Command("add_section"))
+    dp.message.register(del_section_cmd, Command("del_section"))
+    dp.message.register(add_subsection_cmd, Command("add_subsection"))
+    dp.message.register(del_subsection_cmd, Command("del_subsection"))
+    dp.message.register(add_product_cmd, Command("add_product"))
+    dp.message.register(del_product_cmd, Command("del_product"))
+    dp.message.register(users_count_cmd, Command("users_count"))
+    dp.message.register(broadcast_cmd, Command("broadcast"))
 
     dp.callback_query.register(open_catalog, F.data.startswith("open_catalog:"))
+    dp.callback_query.register(open_section, F.data.startswith("open_section:"))
+    dp.callback_query.register(open_subsection, F.data.startswith("open_subsection:"))
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
