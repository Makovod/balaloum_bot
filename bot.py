import os
import re
import logging
import pandas as pd
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
# ID вашего публичного канала (куда выкладываем готовые посты)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")
# ID закрытого Фотобанка (откуда берутся фото). Можно указать ID или username канала.
PHOTO_BANK_ID = os.getenv("PHOTO_BANK_ID", "@your_photobank_username")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Путь для сохранения локального файла остатков
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
        "1. Надішліть сюди актуальний **Excel-файл** з остатками від бухгалтера.\n"
        "2. Бот збереже остатки та зможе автоматично обирати ходові моделі.\n"
        "3. Переконайтеся, що фото у Фотобанку підписані артикулом (наприклад, `#9362`).\n\n"
        "Натисніть кнопку нижче для генерації публікації!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


# Прием Excel-файла с остатками
@dp.message(F.document)
async def handle_excel_upload(message: types.Message):
    document = message.document
    if not (document.file_name.endswith(".xlsx") or document.file_name.endswith(".xls")):
        await message.answer("⚠️ Будь ласка, надішліть файл у форматі Excel (`.xlsx` або `.xls`).")
        return

    file_info = await bot.get_file(document.file_id)
    await bot.download_file(file_info.file_path, STOCK_FILE_PATH)

    try:
        df = pd.read_excel(STOCK_FILE_PATH)
        total_rows = len(df)
        await message.answer(
            f"✅ **Файл остатків успішно оновлено!**\n"
            f"Завантажено позицій: `{total_rows}`.\n\n"
            f"Тепер ви можете використовувати автопостинг по найголовнішим залишкам.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка при читанні Excel-файлу: `{e}`", parse_mode="Markdown")


# Просмотр статуса загруженных остатков
@dp.message(F.text == "📊 Статус остатков")
async def show_stock_status(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("ℹ️ Файл остатків ще не завантажено. Надішліть Excel-файл у цей чат.")
        return

    try:
        df = pd.read_excel(STOCK_FILE_PATH)
        await message.answer(
            f"📊 **Поточний стан бази:**\n"
            f"• Усього рядків у таблиці: `{len(df)}`\n"
            f"• Файл збережено та готовий до роботи.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"⚠️ Помилка читання файлу: `{e}`")


# Генерация автопоста по наибольшему остатку
@dp.message(F.text == "🤖 Автопост (выбрать топ-остаток)")
@dp.message(Command("auto_post"))
async def generate_auto_post(message: types.Message):
    if not os.path.exists(STOCK_FILE_PATH):
        await message.answer("⚠️ Спочатку надішліть Excel-файл з остатками в цей чат!")
        return

    await message.answer("🔍 Аналізую остатки та шукаю модель з найбільшим запасом на складі...")

    try:
        df = pd.read_excel(STOCK_FILE_PATH)

        # Пытаемся найти колонку с артикулом и количеством
        # (Ищем подходящие название колонок или берем первую и третью)
        art_col = None
        qty_col = None
        price_col = None

        for col in df.columns:
            col_str = str(col).lower()
            if any(k in col_str for k in ["арт", "модель", "код", "код товара"]):
                art_col = col
            elif any(k in col_str for k in ["остаток", "кол-во", "количество", "залишок", "шт"]):
                qty_col = col
            elif any(k in col_str for k in ["цена", "ціна", "стоимость"]):
                price_col = col

        if not art_col:
            art_col = df.columns[0]
        if not qty_col and len(df.columns) > 1:
            qty_col = df.columns[1]

        # Сортируем по остатку (убывание)
        df_sorted = df.sort_values(by=qty_col, ascending=False)
        top_item = df_sorted.iloc[0]

        article = str(top_item[art_col]).strip()
        quantity = top_item[qty_col]
        price = top_item[price_col] if price_col else "Запитуйте у менеджера"

        post_text = (
            f"🔥 **ТОП-ПРОПОЗИЦІЯ В НАЯВНОСТІ | BALALOUM** 🔥\n\n"
            f"🏷 **Модель:** `{article}`\n"
            f"📦 **Залишок на складі:** `{quantity} шт` (Великий запас!)\n"
            f"💰 **Ціна:** `{price}`\n\n"
            f"✨ Відмінна якість, ходова модель, швидка відправка.\n"
            f"📲 Для замовлення звертайтесь до менеджерів.\n\n"
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
            f"💡 *Примітка:* Переконайтеся, що у Фотобанку є фото з тегом `#{article}`.",
            reply_markup=inline_kb,
            parse_mode="Markdown",
        )

    except Exception as e:
        await message.answer(f"⚠️ Не вдалося обробити Excel-файл. Перевірте формат: `{e}`", parse_mode="Markdown")


@dp.callback_query(F.data == "publish_auto")
async def publish_auto_post(callback: types.CallbackQuery):
    try:
        # Извлекаем текст из сообщения предпросмотра
        text_to_send = callback.message.text.replace("📋 Сформовано автоматичний пост:\n\n", "")
        await bot.send_message(chat_id=CHANNEL_ID, text=text_to_send, parse_mode="Markdown")
        await callback.message.edit_text("✅ **Пост успішно опубліковано у публічний канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"⚠️ Помилка публікації: `{e}`. Перевірте, чи додано бота в канал.", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
