import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Берем токен из настроек сервера
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот Balaloum успішно запущений та готовий до роботи!")

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
