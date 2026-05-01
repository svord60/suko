import asyncio
import os
import uuid
import tempfile
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import Client
from pyrogram.types import Message as PyroMessage
from pyrogram.errors import SessionPasswordNeeded

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (СЕКРЕТЫ) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# ========== НЕСЕКРЕТНЫЕ НАСТРОЙКИ (ЗАМЕНИ) ==========
TARGET_BOT_USERNAME = "@similarfacesroBot"  # username чужого бота
ADMIN_ID = 6997318168  # твой Telegram ID (узнай у @userinfobot)

# Проверка
if not all([BOT_TOKEN, API_ID, API_HASH, PHONE_NUMBER]):
    raise Exception("Задай BOT_TOKEN, API_ID, API_HASH, PHONE_NUMBER")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
user_client = Client("session", api_id=API_ID, api_hash=API_HASH)

sessions = {}
callbacks_map = {}
waiting_for_code = False
waiting_for_password = False
temp_code_hash = None

# ========== КОНВЕРТ КНОПОК ==========
def convert_buttons(pyro_markup, session_id):
    if not pyro_markup or not pyro_markup.inline_keyboard:
        return None
    new_kb = []
    for row in pyro_markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data:
                fake = f"cb_{session_id}_{uuid.uuid4().hex[:8]}"
                callbacks_map[fake] = btn.callback_data
                new_row.append(InlineKeyboardButton(text=btn.text, callback_data=fake))
            elif btn.url:
                new_row.append(InlineKeyboardButton(text=btn.text, url=btn.url))
        if new_row:
            new_kb.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_kb) if new_kb else None

async def download_photo_from_pyrogram(message: PyroMessage) -> str:
    if not message.photo:
        return None
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_path = temp_file.name
    temp_file.close()
    await message.download(file_name=temp_path)
    return temp_path

# ========== ЛОВИМ ОТВЕТЫ ОТ ЦЕЛЕВОГО БОТА ==========
@user_client.on_message()
async def catch_new_message(client: Client, message: PyroMessage):
    if not message.from_user or message.from_user.username != TARGET_BOT_USERNAME.lstrip('@'):
        return
    
    for sid, data in sessions.items():
        if data.get("waiting"):
            text = message.text or message.caption or "✅"
            markup = convert_buttons(message.reply_markup, sid)
            
            if message.photo:
                photo_path = await download_photo_from_pyrogram(message)
                with open(photo_path, "rb") as photo_file:
                    sent = await bot.send_photo(
                        chat_id=data["user_id"],
                        photo=types.InputFile(photo_file),
                        caption=text,
                        reply_markup=markup
                    )
                os.unlink(photo_path)
            else:
                sent = await bot.send_message(
                    chat_id=data["user_id"],
                    text=text,
                    reply_markup=markup
                )
            
            sessions[sid]["bot_msg_id"] = sent.message_id
            sessions[sid]["bot_chat_id"] = data["user_id"]
            sessions[sid]["target_chat"] = message.chat.id
            sessions[sid]["target_msg"] = message.id
            sessions[sid]["waiting"] = False
            break

@user_client.on_edited_message()
async def catch_edited_message(client: Client, message: PyroMessage):
    if not message.from_user or message.from_user.username != TARGET_BOT_USERNAME.lstrip('@'):
        return
    
    for sid, data in sessions.items():
        if data.get("target_msg") == message.id:
            text = message.text or message.caption or "✅"
            markup = convert_buttons(message.reply_markup, sid)
            
            if message.photo:
                photo_path = await download_photo_from_pyrogram(message)
                with open(photo_path, "rb") as photo_file:
                    await bot.edit_message_media(
                        chat_id=data["bot_chat_id"],
                        message_id=data["bot_msg_id"],
                        media=types.InputMediaPhoto(
                            media=types.InputFile(photo_file),
                            caption=text
                        ),
                        reply_markup=markup
                    )
                os.unlink(photo_path)
            else:
                await bot.edit_message_text(
                    chat_id=data["bot_chat_id"],
                    message_id=data["bot_msg_id"],
                    text=text,
                    reply_markup=markup
                )
            
            sessions[sid]["target_msg"] = message.id
            break

# ========== ОБРАБОТКА ФОТО ОТ ПОЛЬЗОВАТЕЛЯ ==========
@dp.message_handler(content_types=['photo'])
async def handle_photo(message: types.Message):
    try:
        await user_client.get_me()
    except:
        await message.answer("❌ Бот не авторизован")
        return
    
    user_id = message.from_user.id
    session_id = str(uuid.uuid4())
    
    status_msg = await message.answer("🔍 Поиск...")
    
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded = await bot.download_file(file_info.file_path)
    
    temp_path = f"/tmp/p_{session_id}.jpg"
    with open(temp_path, "wb") as f:
        f.write(downloaded)
    
    sessions[session_id] = {
        "user_id": user_id,
        "waiting": True,
        "bot_msg_id": None,
        "bot_chat_id": None,
        "target_msg": None
    }
    
    try:
        async with user_client:
            await user_client.send_photo(chat_id=TARGET_BOT_USERNAME, photo=temp_path)
        
        for _ in range(60):
            await asyncio.sleep(1)
            if not sessions.get(session_id, {}).get("waiting", True):
                break
        else:
            await status_msg.edit_text("❌ Таймаут")
        
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка")
    finally:
        if sessions.get(session_id, {}).get("waiting", False):
            sessions.pop(session_id, None)
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ========== ОБРАБОТКА НАЖАТИЙ НА КНОПКИ ==========
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('cb_'))
async def handle_callback(callback_query: types.CallbackQuery):
    fake_cb = callback_query.data
    
    if fake_cb not in callbacks_map:
        await callback_query.answer("❌")
        return
    
    real_cb = callbacks_map[fake_cb]
    parts = fake_cb.split('_')
    session_id = parts[1] if len(parts) > 1 else None
    
    session_info = sessions.get(session_id, {})
    target_chat = session_info.get("target_chat")
    target_msg = session_info.get("target_msg")
    
    if not target_chat or not target_msg:
        await callback_query.answer("❌")
        return
    
    await callback_query.answer("🔄")
    
    try:
        async with user_client:
            await user_client.request_callback_answer(
                chat_id=target_chat,
                message_id=target_msg,
                callback_data=real_cb
            )
        sessions[session_id]["waiting"] = True
        
    except Exception as e:
        await callback_query.message.answer(f"❌ Ошибка")

# ========== АВТОРИЗАЦИЯ ЧЕРЕЗ БОТА ==========
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    global waiting_for_code, waiting_for_password, temp_code_hash
    
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён")
        return
    
    try:
        me = await user_client.get_me()
        await message.answer(f"✅ Уже авторизован как {me.first_name}")
        return
    except:
        pass
    
    await message.answer(f"🔐 Авторизация для {PHONE_NUMBER}\nОтправляю код...")
    
    try:
        await user_client.connect()
        sent_code = await user_client.send_code(PHONE_NUMBER)
        temp_code_hash = sent_code.phone_code_hash
        waiting_for_code = True
        await message.answer("📱 Введи код из Telegram (только цифры):")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message_handler(lambda m: m.from_user.id == ADMIN_ID)
async def handle_admin_input(message: types.Message):
    global waiting_for_code, waiting_for_password, temp_code_hash
    
    text = message.text.strip()
    
    if waiting_for_code:
        try:
            await user_client.sign_in(PHONE_NUMBER, text, phone_code_hash=temp_code_hash)
            await message.answer("✅ Авторизация успешна! Бот работает.")
            waiting_for_code = False
            temp_code_hash = None
        except SessionPasswordNeeded:
            waiting_for_code = False
            waiting_for_password = True
            await message.answer("🔐 Введи пароль двухфакторной аутентификации:")
        except Exception as e:
            error = str(e)
            if "CODE_INVALID" in error or "PHONE_CODE_INVALID" in error:
                await message.answer("❌ Неверный код. Введи ещё раз (только цифры):")
            else:
                await message.answer(f"❌ Ошибка: {error[:100]}")
    
    elif waiting_for_password:
        try:
            await user_client.check_password(text)
            await message.answer("✅ Авторизация успешна! Бот работает.")
            waiting_for_password = False
        except Exception as e:
            await message.answer(f"❌ Неверный пароль. Попробуй ещё раз:")

# ========== ЗАПУСК ==========
async def main():
    print("🚀 Запуск...")
    try:
        await user_client.start()
        me = await user_client.get_me()
        print(f"✅ Аккаунт {me.first_name} авторизован")
    except:
        print("⚠️ Нет сессии. Напиши /start админу")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())