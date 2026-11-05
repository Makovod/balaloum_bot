import asyncio
import json
import logging
import os
import re
import pandas as pd
import pdfplumber
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAW_CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()


def get_target_channel_id():
    if not RAW_CHANNEL_ID:
        return None
    if RAW_CHANNEL_ID.startswith("-") and RAW_CHANNEL_ID[1:].isdigit():
        return int(RAW_CHANNEL_ID)
    if RAW_CHANNEL_ID.isdigit():
        return int(RAW_CHANNEL_ID)
    return RAW_CHANNEL_ID


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

STOCK_FILE_PATH = "latest_stock.xlsx"
PHOTO_DB_PATH = "photo_bank.json"

# Хранилище текущего индекса просмотра для каждого пользователя (для кнопки "Інша модель")
user_post_index = {}


def load_photo_db() -> dict:
    if os.path.exists(PHOTO_DB_PATH):
        try:
            with open(PHOTO_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_photo_db(db: dict):
    with open(PHOTO_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def normalize_tag(text: str) -> str:
    """Приводит подпись под фото (например, '#9362_пудра' или '9362 пудра') к единому ключу '9362_пудра'."""
    if not text:
        return ""
    clean = text.strip().lower().lstrip("#")
    clean = re.sub(r"\s+", "_", clean)
    return clean


def parse_1c_pdf_to_dataframe(pdf_path: str) -> pd.DataFrame:
    """
    Точный координатный парсер ведомости 1С (PDF) для Balaloum.
    Разбирает иерархию: Бренд -> Категория -> Модель + Чашка -> Цвет -> 4-я колонка (Итог).
    """
    def parse_qty(val_str: str) -> int:
        if not val_str:
            return 0
        s = val_str.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            return int(round(float(s)))
        except ValueError:
            return 0

    all_rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=2)
            words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
            lines_by_y = []
            for w in words:
                txt = w["text"].strip()
                if not txt:
                    continue
                if not lines_by_y:
                    lines_by_y.append([w])
                else:
                    prev_y = sum(item["top"] for item in lines_by_y[-1]) / len(lines_by_y[-1])
                    if abs(w["top"] - prev_y) <= 3.2:
                        lines_by_y[-1].append(w)
                    else:
                        lines_by_y.append([w])

            for line_words in lines_by_y:
                line_words.sort(key=lambda w: w["x0"])
                left_words = [w for w in line_words if w["x0"] < 240]
                col4_words = [w for w in line_words if w["x0"] >= 480]  # 4-я колонка "Итог"

                left_x = round(left_words[0]["x0"], 1) if left_words else None
                left_text = " ".join(w["text"] for w in left_words).strip()
                c4 = parse_qty("".join(w["text"] for w in col4_words))
                all_rows.append((left_x, left_text, c4))

    brand = ""
    category = ""
    model_raw = ""
    records = []
    cup_map = {"В": "B", "B": "B", "С": "C", "C": "C", "D": "D", "Е": "E", "E": "E"}

    for x_val, label, c4 in all_rows:
        if x_val is not None and x_val < 41.0:
            continue
        elif x_val is not None and 41.0 <= x_val < 48.0:
            brand = label.rstrip(" ,")
            category = "Основное"
        elif label.endswith("шт"):
            model_raw = label
        elif x_val is not None and 48.0 <= x_val < 55.0 and not label.endswith("шт"):
            category = label.rstrip(" ,")
        else:
            if not model_raw or c4 <= 0:
                continue
            color_raw = label.strip() if label else "стандарт"
            clean_model = re.sub(r"\s*,\s*шт$", "", model_raw).strip()
            clean_no_year = re.sub(r"\(\s*20\d{2}\s*г?\s*\)|\(\s*\d{2}\s*г?\s*\)", "", clean_model).strip()
            clean_no_year = re.sub(r"\s+", " ", clean_no_year)

            cup = "-"
            actual_color = color_raw
            m_col_cup = re.match(r"^([BВCСDЕE])\s*,\s*(.+)$", color_raw)
            if m_col_cup:
                cup = cup_map.get(m_col_cup.group(1), m_col_cup.group(1))
                actual_color = m_col_cup.group(2).strip()
            else:
                m_cup = re.search(r"(?:\d)\s*([BВCСDЕE])(?:\s+бюсты|\s*$)", clean_no_year)
                if m_cup:
                    cup = cup_map.get(m_cup.group(1), m_cup.group(1))

            m_art = re.search(r"(\d{3,6}(?:/\d)?)", clean_no_year)
            article = m_art.group(1) if m_art else clean_no_year

            if actual_color and actual_color != "стандарт":
                photo_tag = f"{article}_{actual_color.lower()}"
            else:
                photo_tag = f"{article}".lower()

            records.append({
                "brand": brand,
                "category": category,
                "article": article,
                "cup": cup,
                "color": actual_color,
                "photo_tag": photo_tag,
                "quantity": c4,
            })

    df_det = pd.DataFrame(records)
    if df_det.empty:
        return df_det

    # Группируем одинаковые (Артикул + Цвет), собирая все чашки B, C, D вместе
    grouped = []
    for (brd, cat, art, col, ptag), grp in df_det.groupby(
        ["brand", "category", "article", "color", "photo_tag"], as_index=False
    ):
        total_q = int(grp["quantity"].sum())
        cups_str = ", ".join(
            f"Чашка {r['cup']}: {int(r['quantity'])} шт" if r["cup"] != "-" else f"{int(r['quantity'])} шт"
            for _, r in grp.sort_values("cup").iterrows()
        )
        grouped.append({
            "brand": brd,
            "category": cat,
            "article": str(art),
            "color": col,
            "photo_tag": ptag,
            "cups_info": cups_str,
            "quantity": total_q,
        })

    return pd.DataFrame(grouped).sort_values(by="quantity", ascending=False)


def main_keyboard():
    kb = [
        [KeyboardButton(text="🤖 Автопост (выбрать топ-остаток)")],
        [KeyboardButton(text="📊 Статус остатков"), KeyboardButton(text="ℹ️ Инструкция")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    photo_db = load_photo_db()
    await message.answer(
        "👋 **Вітаю в автономному боті Balaloum!**\n\n"
        "📁 **Як почати:**\n"
        "1. Надішліть сюди актуальний файл з остатками (Excel `.xlsx` або **PDF**).\n"
        "2. Додайте фото з підписом (наприклад `9362_пудра`) у канал Фотобанку (де бот є адміном) або просто скиньте фото з підписом сюди в чат з ботом.\n"
        "3. Натисніть кнопку нижче для генерації автопоста!\n\n"
        f"📸 У базі Фотобанку зараз збережено фото: `{len(photo_db)}` шт.",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "ℹ️ Инструкция")
async def show_instructions(message: types.Message):
    target_id = get_target_channel_id()
    await message.answer(
        "ℹ️ **Інструкція з роботи бота:**\n\n"
        "1️⃣ **Завантаження залишків:** Надішліть PDF-файл відомості 1С. Бот автоматично розпізнає моделі, кольори, чашки (B, C, D, E) та точний підсумок по 4-й колонці.\n\n"
        "2️⃣ **Як працює Фотобанк:**\n"
        "Бот автоматично запам'ятовує всі фото з підписами типу `9362_пудра`, які ви публікуєте в каналі **Фотобанк Balaloum** (головне — додайте цього бота в адміністратори Фотобанку!). Також ви можете просто переслати будь-яке фото з підписом `9362_пудра` прямо сюди в особистий чат боту.\n\n"
        f"3️⃣ **Канал для публікації (`CHANNEL_ID`):** `{target_id}`\n"
        "Переконайтеся, що бот доданий як **Адміністратор** у цей цільовий канал з правом публікації повідомлень.",
        parse_mode="Markdown",
    )


# --- АВТОМАТИЧЕСКОЕ СОХРАНЕНИЕ ФОТО ИЗ КАНАЛА-ФОТОБАНКА ИЛИ ИЗ ЛИЧКИ БОТА ---
@dp.channel_post(F.photo)
async def handle_channel_photo(message: types.Message):
    caption = message.caption or ""
    tag = normalize_tag(caption)
    if not tag:
        return
    file_id = message.photo[-1].file_id
    db = load_photo_db()
    db[tag] = file_id
    # Также сохраняем базовый артикул (например '9362'), если ищут без цвета
    base_art = tag.split("_")[0]
    if base_art not in db:
        db[base_art] = file_id
    save_photo_db(db)
    logging.info(f"Збережено фото з каналу Фотобанку: тег '{tag}'")


@dp.message(F.photo)
async def handle_private_photo(message: types.Message):
    caption = message.caption or ""
    tag = normalize_tag(caption)
    if not tag:
        await message.answer("⚠️ Щоб зберегти фото в базу, додайте підпис до фото (наприклад: `9362_пудра`).", parse_mode="Markdown")
        return
    file_id = message.photo[-1].file_id
    db = load_photo_db()
    db[tag] = file_id
    base_art = tag.split("_")[0]
    if base_art not in db:
        db[base_art] = file_id
    save_photo_db(db)
    await message.answer(
        f"✅ **Фото збережено у Фотобанк бота!**\n"
        f"🏷 Прив'язаний тег: `{tag}`\n"
        f"📚 Усього фото в базі: `{len(db)}`",
        parse_mode="Markdown",
    )


@dp.message(F.document)
async def handle_document_upload(message: types.Message):
    document = message.document
    file_name = document.file_name.lower()

    if not (file_name.endswith(".xlsx") or file_name.endswith(".xls") or file_name.endswith(".pdf")):
        await message.answer("⚠️ Будь ласка, надішліть файл у форматі Excel (`.xlsx`) або **PDF**.")
        return

    wait_msg = await message.answer("⏳ Завантажую та розбираю файл по колонках, чашках і кольорах...")
    file_info = await bot.get_file(document.file_id)
    downloaded_path = "temp_file" + os.path.splitext(file_name)[1]
    await bot.download_file(file_info.file_path, downloaded_path)

    try:
        if file_name.endswith(".pdf"):
            df = parse_1c_pdf_to_dataframe(downloaded_path)
            if df.empty:
                await wait_msg.edit_text("⚠️ Не вдалося знайти товари в PDF-файлі.")
                return
            df.to_excel(STOCK_FILE_PATH, index=False)
        else:
            df = pd.read_excel(downloaded_path)
            df.to_excel(STOCK_FILE_PATH, index=False)

        if os.path.exists(downloaded_path):
            os.remove(downloaded_path)

        total_rows = len(df)
        total_units = int(df["quantity"].sum()) if "quantity" in df.columns else 0
        user_post_index[message.from_user.id] = 0

        await wait_msg.edit_text(
            f"✅ **Файл залишків успішно оброблено!**\n"
            f"• Унікальних позицій (модель + колір): `{total_rows}`\n"
            f"• Загальна кількість на складі (4-та колонка): `{total_units} шт.`\n\n"
            f"Тепер натисніть кнопку **🤖 Автопост (выбрать топ-остаток)**.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Помилка при обробці файлу: `{e}`", parse_mode="Markdown")


@dp.message(F.text == "📊 Статус остатков")
async def show_stock_status(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("ℹ️ Файл остатків ще не завантажено.")
        return

    try:
        df = pd.read_excel(STOCK_FILE_PATH)
        photo_db = load_photo_db()
        total_units = int(df["quantity"].sum()) if "quantity" in df.columns else 0
        await message.answer(
            f"📊 **Поточний стан бази:**\n"
            f"• Позицій (модель + колір): `{len(df)}`\n"
            f"• Сумарний залишок: `{total_units} шт.`\n"
            f"• Збережених фото у Фотобанку бота: `{len(photo_db)}`\n"
            f"• Цільовий канал (`CHANNEL_ID`): `{get_target_channel_id()}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка читання файлу: `{e}`")


async def send_candidate_preview(chat_id: int, user_id: int, index: int = 0):
    df = pd.read_excel(STOCK_FILE_PATH)
    if df.empty:
        await bot.send_message(chat_id, "⚠️ База порожня. Спочатку завантажте PDF.")
        return

    photo_db = load_photo_db()

    # Проверяем, для каких позиций уже есть фото в базе (по тегу '9362_пудра' или артикулу '9362')
    df["photo_tag_str"] = df["photo_tag"].astype(str).str.lower()
    df["article_str"] = df["article"].astype(str).str.lower()
    df["has_photo"] = df["photo_tag_str"].isin(photo_db.keys()) | df["article_str"].isin(photo_db.keys())

    # Сначала идут товары с загруженными фото (по убыванию остатка), затем остальные топ-остатки
    df_sorted = df.sort_values(by=["has_photo", "quantity"], ascending=[False, False]).reset_index(drop=True)

    index = index % len(df_sorted)
    user_post_index[user_id] = index
    item = df_sorted.iloc[index]

    article = str(item["article"])
    color = str(item.get("color", ""))
    photo_tag = str(item.get("photo_tag", f"{article}_{color}")).lower()
    cups_info = str(item.get("cups_info", ""))
    category = str(item.get("category", "Білизна"))
    quantity = int(item["quantity"])

    file_id = photo_db.get(photo_tag) or photo_db.get(article.lower())

    post_text = (
        f"🔥 **ТОП-ПРОПОЗИЦІЯ В НАЯВНОСТІ | BALALOUM** 🔥\n\n"
        f"🏷 **Модель / Артикул:** `{article}` ({category})\n"
        f"🎨 **Колір:** `{color}`\n"
        f"👙 **Наявність по чашках:** {cups_info}\n"
        f"📦 **Загальний залишок:** `{quantity} шт`\n"
        f"💰 **Ціна:** Запитуйте у менеджера / Опт\n\n"
        f"✨ Відмінна якість, актуальна наявність на складі.\n"
        f"📲 Для замовлення звертайтесь до менеджерів.\n\n"
        f"#{photo_tag} #balaloum #гурт #білизнаоптом"
    )

    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Опублікувати в канал", callback_data=f"pub:{index}"),
                InlineKeyboardButton(text="🔄 Інша модель", callback_data=f"next:{index + 1}"),
            ]
        ]
    )

    status_note = (
        f"✅ *Фото з Фотобанку знайдено за тегом `{photo_tag}`!*"
        if file_id
        else f"💡 *Фото для `{photo_tag}` ще немає в пам'яті бота. Перешліть фото з підписом `{photo_tag}` у цей чат або додайте в канал Фотобанку.*"
    )

    full_preview_text = f"📋 **Сформовано автоматичний пост ({index + 1} з {len(df_sorted)}):**\n\n{post_text}\n\n{status_note}"

    if file_id:
        await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=full_preview_text,
            reply_markup=inline_kb,
            parse_mode="Markdown",
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=full_preview_text,
            reply_markup=inline_kb,
            parse_mode="Markdown",
        )


@dp.message(F.text == "🤖 Автопост (выбрать топ-остаток)")
@dp.message(Command("auto_post"))
async def generate_auto_post(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("⚠️ Спочатку надішліть PDF-файл з остатками в цей чат!")
        return

    idx = user_post_index.get(message.from_user.id, 0)
    await send_candidate_preview(message.chat.id, message.from_user.id, idx)


@dp.callback_query(F.data.startswith("next:"))
async def next_auto_post(callback: types.CallbackQuery):
    next_idx = int(callback.data.split(":")[1])
    await callback.answer("🔄 Перемикаю на наступну модель...")
    await send_candidate_preview(callback.message.chat.id, callback.from_user.id, next_idx)


@dp.callback_query(F.data.startswith("pub:"))
async def publish_auto_post(callback: types.CallbackQuery):
    target_channel = get_target_channel_id()
    if not target_channel:
        await callback.message.answer("⚠️ Змінна `CHANNEL_ID` не налаштована в Railway!")
        return

    try:
        idx = int(callback.data.split(":")[1])
        df = pd.read_excel(STOCK_FILE_PATH)
        photo_db = load_photo_db()

        df["photo_tag_str"] = df["photo_tag"].astype(str).str.lower()
        df["article_str"] = df["article"].astype(str).str.lower()
        df["has_photo"] = df["photo_tag_str"].isin(photo_db.keys()) | df["article_str"].isin(photo_db.keys())
        df_sorted = df.sort_values(by=["has_photo", "quantity"], ascending=[False, False]).reset_index(drop=True)

        item = df_sorted.iloc[idx % len(df_sorted)]
        article = str(item["article"])
        color = str(item.get("color", ""))
        photo_tag = str(item.get("photo_tag", f"{article}_{color}")).lower()
        cups_info = str(item.get("cups_info", ""))
        category = str(item.get("category", "Білизна"))
        quantity = int(item["quantity"])

        file_id = photo_db.get(photo_tag) or photo_db.get(article.lower())

        clean_post_text = (
            f"🔥 **ТОП-ПРОПОЗИЦІЯ В НАЯВНОСТІ | BALALOUM** 🔥\n\n"
            f"🏷 **Модель / Артикул:** `{article}` ({category})\n"
            f"🎨 **Колір:** `{color}`\n"
            f"👙 **Наявність по чашках:** {cups_info}\n"
            f"📦 **Загальний залишок:** `{quantity} шт`\n"
            f"💰 **Ціна:** Запитуйте у менеджера / Опт\n\n"
            f"✨ Відмінна якість, актуальна наявність на складі.\n"
            f"📲 Для замовлення звертайтесь до менеджерів.\n\n"
            f"#{photo_tag} #balaloum #гурт #білизнаоптом"
        )

        if file_id:
            await bot.send_photo(
                chat_id=target_channel,
                photo=file_id,
                caption=clean_post_text,
                parse_mode="Markdown",
            )
        else:
            await bot.send_message(
                chat_id=target_channel,
                text=clean_post_text,
                parse_mode="Markdown",
            )

        await callback.answer("✅ Опубліковано!")
        await callback.message.answer(f"✅ **Пост `{photo_tag}` успішно опубліковано в канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(
            f"⚠️ Помилка публікації в канал `{target_channel}`:\n`{e}`\n\n"
            f"👉 **Як виправити:** Зайдіть у ваш канал (ID `{target_channel}`), відкрийте **Адміністратори -> Додати адміністратора**, знайдіть цього бота (`Joy` / `@balaloum_bot`) і увімкніть тумблер **Публікація повідомлень**.",
            parse_mode="Markdown",
        )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
