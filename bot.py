import os
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Укажите ID вашего Telegram-канала (например, "@balaloum_opt" или "-100xxxxxxxxx")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# Состояния FSM для пошагового создания поста
class PostState(StatesGroup):
    photo = State()
    model = State()
    sizes = State()
    price = State()
    description = State()
    confirm = State()


# Главная клавиатура
def main_keyboard():
    kb = [
        [KeyboardButton(text="📢 Створити новий пост")],
        [KeyboardButton(text="ℹ️ Довідка")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# Старт
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Бот Balaloum успішно запущений та готовий до роботи!**\n\n"
        "За допомогою цього бота ви можете швидко створювати та публікувати "
        "гуртові пости з білизною Balaloum у свій Telegram-канал.",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


# Начало создания поста
@dp.message(F.text == "📢 Створити новий пост")
@dp.message(Command("post"))
async def start_post(message: types.Message, state: FSMContext):
    await state.set_state(PostState.photo)
    await message.answer(
        "📸 **Крок 1/5:** Надішліть фотографію товару (або перешлите з Фотобанку)."
    )


# Шаг 1: Фото
@dp.message(PostState.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(PostState.model)
    await message.answer("👙 **Крок 2/5:** Введіть артикул / назву моделі (наприклад, `9350 Balaloum`):", parse_mode="Markdown")


# Шаг 2: Модель
@dp.message(PostState.model)
async def process_model(message: types.Message, state: FSMContext):
    await state.update_data(model=message.text.strip())
    await state.set_state(PostState.sizes)
    await message.answer("📏 **Крок 3/5:** Вкажіть доступні розміри в упаковці (наприклад, `B 75-80-85 (6 шт)`):")


# Шаг 3: Размеры
@dp.message(PostState.sizes)
async def process_sizes(message: types.Message, state: FSMContext):
    await state.update_data(sizes=message.text.strip())
    await state.set_state(PostState.price)
    await message.answer("💰 **Крок 4/5:** Вкажіть ціну (наприклад, `380 грн / шт` або `2280 грн / ростовка`):")


# Шаг 4: Цена
@dp.message(PostState.price)
async def process_price(message: types.Message, state: FSMContext):
    await state.update_data(price=message.text.strip())
    await state.set_state(PostState.description)
    await message.answer(
        "📝 **Крок 5/5:** Додайте опис або колір/особливості (наприклад, `М'яка чашка, подвійний пуш-ап`). "
        "Якщо опис не потрібен, напишіть `-`:"
    )


# Шаг 5: Описание и предпросмотр
@dp.message(PostState.description)
async def process_description(message: types.Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state.update_data(description=desc)

    data = await state.get_data()

    # Формирование рекламного поста в украинской стилистике
    post_text = (
        f"✨ **NEW COLLECTION | BALALOUM** ✨\n\n"
        f"🏷 **Модель:** {data['model']}\n"
        f"📏 **Розміри:** {data['sizes']}\n"
        f"💰 **Ціна:** {data['price']}\n"
    )
    if data['description']:
        post_text += f"ℹ️ **Опис:** {data['description']}\n"

    post_text += (
        "\n📦 *В наявності на складі. Готово до відправки!*\n"
        "📲 Для замовлення пишіть в ПП або менеджерам.\n\n"
        "#balaloum #гурт #білизнаоптом #новинки"
    )

    await state.update_data(final_text=post_text)
    await state.set_state(PostState.confirm)

    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Опублікувати в канал", callback_data="publish_post"),
                InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_post"),
            ]
        ]
    )

    await message.answer_photo(
        photo=data['photo_id'],
        caption=f"📋 **Попередній перегляд поста:**\n\n{post_text}",
        reply_markup=inline_kb,
        parse_mode="Markdown",
    )


# Обработка нажатия «Опубликовать»
@dp.callback_query(PostState.confirm, F.data == "publish_post")
async def publish_post(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=data['photo_id'],
            caption=data['final_text'],
            parse_mode="Markdown",
        )
        await callback.message.edit_caption(caption="✅ **Пост успішно опубліковано в канал!**", parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(
            f"⚠️ Помилка при публікації в канал. Перевірте, чи додано бота в адміністратори каналу.\nДеталі: `{e}`",
            parse_mode="Markdown"
        )
    await state.clear()


# Обработка отмены
@dp.callback_query(PostState.confirm, F.data == "cancel_post")
async def cancel_post(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_caption(caption="❌ Публікацію скасовано.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
