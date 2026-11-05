import os
import re
import logging
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
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

STOCK_FILE_PATH = "latest_stock.xlsx"


def main_keyboard():
    kb = [
        [KeyboardButton(text="🤖 Автопост (выбрать топ-остаток)")],
        [KeyboardButton(text="📊 Статус остатков"), KeyboardButton(text="ℹ️ Инструкция")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Вітаю в автономному боті Balaloum!**\n\n"
        "📁 **Як почати:**\n"
        "1. Надішліть сюди актуальний файл з остатками (Excel `.xlsx` або **PDF**).\n"
        "2. Бот розпарсить товари за підсумковою кількістю.\n"
        "3. Натисніть кнопку нижче для генерації автопоста!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


@dp.message(F.document)
async def handle_document_upload(message: types.Message):
    document = message.document
    file_name = document.file_name.lower()
    
    if not (file_name.endswith(".xlsx") or file_name.endswith(".xls") or file_name.endswith(".pdf")):
        await message.answer("⚠️ Будь ласка, надішліть файл у форматі Excel (`.xlsx`) або **PDF**.")
        return

    file_info = await bot.get_file(document.file_id)
    downloaded_path = "temp_file" + os.path.splitext(file_name)[1]
    await bot.download_file(file_info.file_path, downloaded_path)

    try:
        if file_name.endswith(".pdf"):
            tables_data = []
            with pdfplumber.open(downloaded_path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table:
                        tables_data.extend(table)
            
            if len(tables_data) > 0:
                df = pd.DataFrame(tables_data)
            else:
                await message.answer("⚠️ У цьому PDF-файлі не вдалося знайти таблиці.")
                return
            
            df.to_excel(STOCK_FILE_PATH, index=False)
            if os.path.exists(downloaded_path):
                os.remove(downloaded_path)
        else:
            df = pd.read_excel(downloaded_path)
            df.to_excel(STOCK_FILE_PATH, index=False)
            if downloaded_path != STOCK_FILE_PATH and os.path.exists(downloaded_path):
                os.remove(downloaded_path)

        df_final = pd.read_excel(STOCK_FILE_PATH)
        total_rows = len(df_final)
        await message.answer(
            f"✅ **Файл остатків з PDF успішно збережено!**\n"
            f"Знайдено рядків: `{total_rows}`.\n\n"
            f"Тепер натисніть кнопку **🤖 Автопост (выбрать топ-остаток)**.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка при обробці файлу: `{e}`", parse_mode="Markdown")


@dp.message(F.text == "📊 Статус остатков")
async def show_stock_status(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("ℹ️ Файл остатків ще не завантажено.")
        return

    try:
        df = pd.read_excel(STOCK_FILE_PATH)
        await message.answer(
            f"📊 **Поточний стан бази:**\n"
            f"• Усього позицій/рядків: `{len(df)}`\n"
            f"• Файл готовий до роботи.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка читання файлу: `{e}`")


@dp.message(F.text == "🤖 Автопост (выбрать топ-остаток)")
@dp.message(Command("auto_post"))
async def generate_auto_post(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("⚠️ Спочатку надішліть файл з остатками в цей чат!")
        return

    await message.answer("🔍 Аналізую остатки з PDF та шукаю модель з найбільшим запасом...")

    try:
        df = pd.read_excel(STOCK_FILE_PATH)

        # Берем 1-ю колонку (индекс 0) для наименования/артикула
        # и 4-ю колонку (индекс 3) для общего количества
        name_col_idx = 0
        qty_col_idx = 3 if len(df.columns) >= 4 else len(df.columns) - 1

        parsed_items = []

        for idx, row in df.iterrows():
            raw_name = str(row.iloc[name_col_idx]).strip()
            raw_qty = str(row.iloc[qty_col_idx]).strip()

            # Игнорируем служебные строки ("Белье", "Наименование", "Итого" и т.д.)
            if not raw_name or any(k in raw_name.lower() for k in ["белье", "наименование", "итого", "баланс"]):
                continue

            # Преобразуем количество в число
            clean_qty_str = re.sub(r"[^\d.]", "", raw_qty)
            try:
                qty_val = float(clean_qty_str) if clean_qty_str else 0.0
            except ValueError:
                qty_val = 0.0

            if qty_val <= 0:
                continue

            # Ищем номер артикула в строке (например, 9063, 9362, 9063c)
            art_match = re.search(r"(\d{3,5}\s?[a-zA-Zа-яА-Я]?)", raw_name)
            article_code = art_match.group(1).replace(" ", "") if art_match else raw_name

            parsed_items.append({
                "raw_name": raw_name,
                "article": article_code,
                "quantity": qty_val
            })

        if not parsed_items:
            await message.answer("⚠️ Не вдалося витягнути товари з остатком більше 0. Перевірте файл.")
            return

        # Сортируем по убыванию общего количества (колонка "Итого")
        parsed_items.sort(key=lambda x: x["quantity"], reverse=True)
        top_item = parsed_items[0]

        article = top_item["article"]
        quantity = int(top_item["quantity"])

        post_text = (
            f"🔥 **ТОП-ПРОПОЗИЦІЯ В НАЯВНОСТІ | BALALOUM** 🔥\n\n"
            f"🏷 **Модель / Артикул:** `{article}`\n"
            f"📦 **Загальний залишок:** `{quantity} шт` (Найбільший запас!)\n"
            f"💰 **Ціна:** Запитуйте у менеджера / Опт\n\n"
            f"✨ Відмінна якість, ходові кольори в наявності.\n"
            f"📲 Для замовлення звертайтесь до менеджерів.\n\n"
            f"#{article} #balaloum #гурт #білизнаоптом"
        )

        inline_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🚀 Опублікувати в канал", callback_data="publish_auto"),
                    InlineKeyboardButton(text="🔄 Інша модель", callback_data="next_auto"),
                ]
            ]
        )

        await message.answer(
            f"📋 **Сформовано автоматичний пост:**\n\n{post_text}\n\n"
            f"💡 *Переконайтеся, що у Фотобанку є фото з тегом `#{article}`.*",
            reply_markup=inline_kb,
            parse_mode="Markdown",
        )

    except Exception as e:
        await message.answer(f"⚠️ Помилка аналізу таблиці z PDF: `{e}`", parse_mode="Markdown")


@dp.callback_query(F.data == "publish_auto")
async def publish_auto_post(callback: types.CallbackQuery):
    try:
        text_to_send = callback.message.text.replace("📋 Сформовано автоматичний пост:\n\n", "")
        text_to_send = text_to_send.split("\n\n💡")[0]
        await bot.send_message(chat_id=CHANNEL_ID, text=text_to_send, parse_mode="Markdown")
        await callback.message.edit_text("✅ **Пост успішно опубліковано у публічний канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"⚠️ Помилка публікації: `{e}`. Перевірте, чи додано бота в канал як адміна.", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
