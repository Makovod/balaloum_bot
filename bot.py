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
    if not text:
        return ""
    clean = text.strip().lower().lstrip("#")
    clean = re.sub(r"\s+", "_", clean)
    return clean


def parse_1c_pdf_to_dataframe(pdf_path: str) -> pd.DataFrame:
    """
    Парсер ведомости 1С (PDF): ищет бренды, категории, модели, чашки и цвета.
    Строго фильтрует мусор (бретели, вешалки, тапочки), оставляя белье и комплекты.
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
                col4_words = [w for w in line_words if w["x0"] >= 480]

                left_x = round(left_words[0]["x0"], 1) if left_words else None
                left_text = " ".join(w["text"] for w in left_words).strip()
                c4 = parse_qty("".join(w["text"] for w in col4_words))
                all_rows.append((left_x, left_text, c4))

    brand = ""
    category = ""
    model_raw = ""
    records = []
    cup_map = {"В": "B", "B": "B", "С": "C", "C": "C", "D": "D", "Е": "E", "E": "E"}

    # Список ключевых слов категорий, которые НАМ НУЖНЫ (белье, комплекты, бюсты, трусы)
    valid_categories = ["комплекты", "бюсты", "трусы", "боди", "белье"]

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
            
            # Отсекаем ненужные разделы (аксессуары, тапочки, вешалки, колготки, спорт, турция и т.д.)
            cat_lower = category.lower()
            model_lower = model_raw.lower()
            if any(bad in cat_lower or bad in model_lower for bad in ["аксесуар", "тапоч", "вешал", "манекен", "колгот", "спорт", "чулк", "эротик", "бретел", "косточ"]):
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

    # Группируем по (Артикул + Цвет), собирая чашки B, C, D вместе
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

    # Сортируем: сначала те позиции, к которым УЖЕ ЕСТЬ фото в базе, затем по остатку
    df_out = pd.DataFrame(grouped)
    return df_out.sort_values(by="quantity", ascending=False)


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
        "📁 **Як користуватися:**\n"
        "1. Надішліть сюди PDF-файл з залишками.\n"
        "2. Скиньте фото у ваш канал-Фотобанк (або сюди в чат) із підписом типу `9362_пудра`.\n"
        "3. Натискайте **🤖 Автопост** — бот відбере гарну білизну та сформує пост із чашками та фото!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "ℹ️ Инструкция")
async def show_instructions(message: types.Message):
    await message.answer(
        "ℹ️ **Інструкція:**\n\n"
        "• Бот автоматично фільтрує товари, залишаючи лише комплекти та білизну з найбільшими залишками.\n"
        "• Для підв'язки фото просто підпишіть фото в каналі-фотобанку або в чаті: `9362_пудра`.\n"
        "• Бот сам покаже наявність по чашках (B, C, D, E) та загальну кількість.",
        parse_mode="Markdown",
    )


@dp.channel_post(F.photo)
async def handle_channel_photo(message: types.Message):
    caption = message.caption or ""
    tag = normalize_tag(caption)
    if not tag:
        return
    file_id = message.photo[-1].file_id
    db = load_photo_db()
    db[tag] = file_id
    base_art = tag.split("_")[0]
    if base_art not in db:
        db[base_art] = file_id
    save_photo_db(db)


@dp.message(F.photo)
async def handle_private_photo(message: types.Message):
    caption = message.caption or ""
    tag = normalize_tag(caption)
    if not tag:
        await message.answer("⚠️ Будь ласка, додайте підпис до фото (наприклад: `9362_пудра`), щоб бот знав, до якого товару його прив'язати.", parse_mode="Markdown")
        return
    file_id = message.photo[-1].file_id
    db = load_photo_db()
    db[tag] = file_id
    base_art = tag.split("_")[0]
    if base_art not in db:
        db[base_art] = file_id
    save_photo_db(db)
    await message.answer(
        f"✅ **Фото успішно збережено у базу!**\n"
        f"🏷 Тег: `{tag}`\n"
        f"📚 Усього фото в пам'яті: `{len(db)}`",
        parse_mode="Markdown",
    )


@dp.message(F.document)
async def handle_document_upload(message: types.Message):
    document = message.document
    file_name = document.file_name.lower()

    if not (file_name.endswith(".xlsx") or file_name.endswith(".xls") or file_name.endswith(".pdf")):
        await message.answer("⚠️ Надішліть файл Excel (`.xlsx`) або **PDF**.")
        return

    wait_msg = await message.answer("⏳ Оброблюю файл залишків, фільтрую аксесуари...")
    file_info = await bot.get_file(document.file_id)
    downloaded_path = "temp_file" + os.path.splitext(file_name)[1]
    await bot.download_file(file_info.file_path, downloaded_path)

    try:
        if file_name.endswith(".pdf"):
            df = parse_1c_pdf_to_dataframe(downloaded_path)
            if df.empty:
                await wait_msg.edit_text("⚠️ Не вдалося знайти товари у файлі.")
                return
            df.to_excel(STOCK_FILE_PATH, index=False)
        else:
            df = pd.read_excel(downloaded_path)
            df.to_excel(STOCK_FILE_PATH, index=False)

        if os.path.exists(downloaded_path):
            os.remove(downloaded_path)

        user_post_index[message.from_user.id] = 0
        await wait_msg.edit_text(
            f"✅ **Готово! База залишків оновлена.**\n"
            f"Відібрано позицій білизни: `{len(df)}`\n\n"
            f"Натисніть кнопку **🤖 Автопост**, щоб переглянути товари.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Помилка: `{e}`", parse_mode="Markdown")


async def send_candidate_preview(chat_id: int, user_id: int, index: int = 0):
    if not os.path.exists(STOCK_FILE_PATH):
        await bot.send_message(chat_id, "⚠️ Спочатку завантажте PDF-файл з залишками!")
        return

    df = pd.read_excel(STOCK_FILE_PATH)
    if df.empty:
        await bot.send_message(chat_id, "⚠️ База порожня.")
        return

    photo_db = load_photo_db()
    df["photo_tag_str"] = df["photo_tag"].astype(str).str.lower()
    df["article_str"] = df["article"].astype(str).str.lower()
    df["has_photo"] = df["photo_tag_str"].isin(photo_db.keys()) | df["article_str"].isin(photo_db.keys())

    # Сначала показываем те позиции, к которым есть фото в базе
    df_sorted = df.sort_values(by=["has_photo", "quantity"], ascending=[False, False]).reset_index(drop=True)

    index = index % len(df_sorted)
    user_post_index[user_id] = index
    item = df_sorted.iloc[index]

    article = str(item["article"])
    color = str(item.get("color", ""))
    photo_tag = str(item.get("photo_tag", f"{article}_{color}")).lower()
    cups_info = str(item.get("cups_info", ""))
    category = str(item.get("category", "Комплект"))
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
        f"✅ *Фото знайдено за тегом `{photo_tag}`!*"
        if file_id
        else f"💡 *Фото для `{photo_tag}` ще немає. Скиньте фото з підписом `{photo_tag}` сюди в чат.*"
    )

    full_text = f"📋 **Попередній перегляд ({index + 1} з {len(df_sorted)}):**\n\n{post_text}\n\n{status_note}"

    if file_id:
        await bot.send_photo(chat_id=chat_id, photo=file_id, caption=full_text, reply_markup=inline_kb, parse_mode="Markdown")
    else:
        await bot.send_message(chat_id=chat_id, text=full_text, reply_markup=inline_kb, parse_mode="Markdown")


@dp.message(F.text == "🤖 Автопост (выбрать топ-остаток)")
@dp.message(Command("auto_post"))
async def generate_auto_post(message: types.Message):
    idx = user_post_index.get(message.from_user.id, 0)
    await send_candidate_preview(message.chat.id, message.from_user.id, idx)


@dp.callback_query(F.data.startswith("next:"))
async def next_auto_post(callback: types.CallbackQuery):
    next_idx = int(callback.data.split(":")[1])
    await callback.answer("🔄 Шукаю наступну модель...")
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
        category = str(item.get("category", "Комплект"))
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
            await bot.send_photo(chat_id=target_channel, photo=file_id, caption=clean_post_text, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=target_channel, text=clean_post_text, parse_mode="Markdown")

        await callback.answer("✅ Успішно опубліковано!")
        await callback.message.answer(f"✅ **Пост `{photo_tag}` опубліковано в канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"⚠️ Помилка публікації: `{e}`", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
