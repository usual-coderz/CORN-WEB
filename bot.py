from pyrogram import Client

api_id = 123456          # my.telegram.org se
api_hash = "your_api_hash"
bot_token = "your_bot_token"
LOG_CHANNEL = -1001234567890  # private channel ID

bot = Client("edu_bot", api_id, api_hash, bot_token=bot_token)

def send_log(name, phone, user_id):
    with bot:
        bot.send_message(
            LOG_CHANNEL,
            f"🎓 NEW LOGIN\n👤 Name: {name}\n📱 Phone: +{phone}\n🆔 TG ID: `{user_id}`\n🕒 Time: logged"
        )