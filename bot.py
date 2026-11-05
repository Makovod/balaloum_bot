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
        "2. Бот збереже та розпарсить дані.\n"
        "3. Переконайтеся, що фото у Фотобанку підписані артикулом.\n\n"
        "Натисніть кнопку нижче для генерації публікації!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


# Прием файлов (Excel или PDF)
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
            # Извлекаем таблицы из PDF и собираем в единый DataFrame через pandas
            tables_data = []
            with pdfplumber.open(downloaded_path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table:
                        tables_data.extend(table)
            
            if len(tables_data) > 1:
                df = pd.DataFrame(tables_data[1:], columns=tables_data[0])
            elif len(tables_data) == 1:
                df = pd.DataFrame(tables_data)
            else:
                await message.answer("⚠️ У цьому PDF-файлі не вдалося знайти таблиці. Перевірте його структуру.")
                return
            
            # Сохраняем в универсальный Excel для удобства работы бота
            df.to_excel(STOCK_FILE_PATH, index=False)
            if os.path.exists(downloaded_path):
                os.remove(downloaded_path)
        else:
            # Если это был обычный Excel
            if downloaded_path != STOCK_FILE_PATH:
                df = pd.read_excel(downloaded_path)
                df.to_excel(STOCK_FILE_PATH, index=False)
                os.remove(downloaded_path)

        df_final = pd.read_excel(STOCK_FILE_PATH)
        total_rows = len(df_final)
        await message.answer(
            f"✅ **Файл остатків з PDF успішно опрацьовано та збережено!**\n"
            f"Знайдено позицій/рядків: `{total_rows}`.\n\n"
            f"Тепер можна генерувати автопостинг.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка при обробці файлу: `{e}`", parse_mode="Markdown")


# Просмотр статуса загруженных остатков
@dp.message(F.text == "📊 Статус остатков")
async def show_stock_status(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("ℹ️ Файл остатків ще не завантажено. Надішліть документ у цей чат.")
        return

    try:
        df = pd.read_excel(STOCK_FILE_PATH)
        await message.answer(
            f"📊 **Поточний стан бази:**\n"
            f"• Усього позицій: `{len(df)}`\n"
            f"• Файл готовий до роботи.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка читання файлу: `{e}`")


# Генерация автопоста по наибольшему остатку
@dp.message(F.text == "🤖 Автопост (выбрать топ-остаток)")
@dp.message(Command("auto_post"))
async def generate_auto_post(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("⚠️ Спочатку надішліть файл з остатками в цей чат!")
        return

    await message.answer("🔍 Аналізую остатки з PDF та шукаю модель з найбільшим запасом...")

    try:
        df = pd.read_excel(STOCK_FILE_PATH)

        art_col = None
        qty_col = None
        price_col = None

        for col in df.columns:
            col_str = str(col).lower()
            if any(k in col_str for k in ["арт", "модель", "код", "наименование", "товар"]):
                art_col = col
            elif any(k in col_str for k in ["остаток", "кол-во", "количество", "залишок", "шт", "к-во"]):
                qty_col = col
            elif any(k in col_str for k in ["цена", "ціна", "стоимость"]):
                price_col = col

        if not art_col:
            art_col = df.columns[0]
        if not qty_col and len(df.columns) > 1:
            qty_col = df.columns[1]

        # Превращаем количество в числа для корректной сортировки
        df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
        df_sorted = df.sort_values(by=qty_col, ascending=False)
        top_item = df_sorted.iloc[0]

        article = str(top_item[art_col]).strip()
        quantity = top_item[qty_col]
        price = top_item[price_col] if price_col and price_col in df.columns else "Запитуйте у менеджера"

        post_text = (
            f"🔥 **ТОП-ПРОПОЗИЦІЯ В НАЯВНОСТІ | BALALOUM** 🔥\n\n"
            f"🏷 **Модель:** `{article}`\n"
            f"📦 **Залишок на складі:** `{quantity} шт`\n"
            f"💰 **Ціна:** `{price}`\n\n"
            f"✨ Висока якість, перевірена модель, швидка відправка.\n"
            f"📲 Для замовлення пишіть у повідомлення.\n\n"
            f"#{article.replace(' ', '_')} #balaloum #гурт #білизнаоптом"
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
        await message.answer(f"⚠️ Помилка аналізу таблиці з PDF: `{e}`", parse_mode="Markdown")


@dp.callback_query(F.data == "publish_auto")
async def publish_auto_post(callback: types.CallbackQuery):
    try:
        text_to_send = callback.message.text.replace("📋 Сформовано автоматичний пост:\n\n", "")
        # Очищаем текст от служебного хвостика про фотобанк перед отправкой в канал
        text_to_send = text_to_send.split("\n\n💡")[0]
        await bot.send_message(chat_id=CHANNEL_ID, text=text_to_send, parse_mode="Markdown")
        await callback.message.edit_text("✅ **Пост успішно опубліковано у публічний канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"⚠️ Помилка публікації: `{e}`", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
