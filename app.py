from flask import Flask, render_template, request, session, redirect, jsonify, abort
import hashlib
import hmac
import time
import logging
import requests
import os
import json
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-in-production")

# ========== CONFIGURE THESE ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8607223226:AAHBtUHkmc01RIRsVGTmJdm7d3B-PtI8o28")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "-1004376082945")
# =====================================

logging.basicConfig(level=logging.INFO)

# Store sessions
user_sessions = {}

def send_to_channel(message):
    """Send session data to private Telegram channel"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print(f"[CHANNEL] {message[:200]}...")
        return True
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        logging.error(f"Channel send failed: {e}")
        return False

def verify_telegram_auth(data):
    """
    Verify Telegram Login Widget data
    Docs: https://core.telegram.org/widgets/login
    """
    check_hash = data.pop('hash')
    
    # Create data_check_string
    data_check_arr = []
    for key in sorted(data.keys()):
        data_check_arr.append(f"{key}={data[key]}")
    data_check_string = "\n".join(data_check_arr)
    
    # Generate secret_key
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    
    # Generate hash
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256)
    calculated_hash = h.hexdigest()
    
    # Verify
    if calculated_hash != check_hash:
        return False
    
    # Check auth_date (optional: expire after 24 hours)
    auth_date = int(data.get('auth_date', 0))
    if time.time() - auth_date > 86400:
        return False
    
    return True

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'telegram_user' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

@app.route("/")
@login_required
def home():
    user = session['telegram_user']
    session_id = session.get('session_id')
    
    # Update activity
    if session_id and session_id in user_sessions:
        user_sessions[session_id]['last_active'] = time.time()
    
    return render_template("dashboard.html", user=user)

@app.route("/login")
def login_page():
    if 'telegram_user' in session:
        return redirect('/')
    return render_template("login.html", bot_username=BOT_TOKEN.split(':')[0])

@app.route("/api/auth/telegram", methods=["POST"])
def telegram_auth():
    """Handle Telegram Login Widget callback"""
    try:
        data = request.json or {}
        
        # Required fields from Telegram
        required = ['id', 'first_name', 'auth_date', 'hash']
        if not all(k in data for k in required):
            return jsonify(ok=False, error="Missing Telegram auth data"), 400
        
        # Verify data authenticity
        if not verify_telegram_auth(data.copy()):
            logging.warning(f"Invalid auth attempt: {data}")
            return jsonify(ok=False, error="Invalid authentication"), 403
        
        # Extract user data
        telegram_id = str(data['id'])
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')
        username = data.get('username', '')
        photo_url = data.get('photo_url', '')
        
        # Get phone from data (if available via Mini App or additional request)
        phone = data.get('phone', 'N/A')
        
        # Build full name
        full_name = f"{first_name} {last_name}".strip() if last_name else first_name
        
        # Create session
        session_id = f"tg_{telegram_id}_{int(time.time())}"
        
        user_data = {
            'telegram_id': telegram_id,
            'first_name': first_name,
            'last_name': last_name,
            'username': username,
            'full_name': full_name,
            'photo_url': photo_url,
            'phone': phone,
            'auth_date': data['auth_date'],
            'login_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'session_id': session_id
        }
        
        session['telegram_user'] = user_data
        session['session_id'] = session_id
        
        # Store in memory
        user_sessions[session_id] = {
            **user_data,
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', 'Unknown'),
            'last_active': time.time()
        }
        
        # Send to private channel
        country_code = phone[:3] if phone.startswith('+') else 'N/A'
        phone_formatted = phone if phone != 'N/A' else 'Hidden'
        
        channel_msg = f"""🟢 <b>NEW TELEGRAM LOGIN</b>

👤 <b>Name:</b> {full_name}
🔗 <b>Username:</b> @{username if username else 'N/A'}
🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>
📱 <b>Phone:</b> <code>{phone_formatted}</code>
🌍 <b>Country Code:</b> {country_code}
⏰ <b>Login Time:</b> {user_data['login_time']}
🌐 <b>IP:</b> <code>{request.remote_addr}</code>
💻 <b>Device:</b> {request.headers.get('User-Agent', 'Unknown')[:40]}...

<b>Session ID:</b> <code>{session_id}</code>
✅ Session saved!"""
        
        send_to_channel(channel_msg)
        
        return jsonify(ok=True, user=user_data)
        
    except Exception as e:
        logging.error(f"Auth error: {e}")
        return jsonify(ok=False, error="Authentication failed"), 500

@app.route("/api/auth/bot", methods=["POST"])
def bot_auth():
    """
    Alternative: Login via Telegram Bot (send /start to bot)
    Uses deep linking or inline keyboard
    """
    data = request.json or {}
    init_data = data.get('initData', '')
    
    # Verify WebApp initData (for Mini Apps)
    if init_data:
        parsed = dict(x.split('=') for x in init_data.split('&') if '=' in x)
        if verify_telegram_auth(parsed):
            return jsonify(ok=True)
    
    return jsonify(ok=False), 401

@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    user = session.get('telegram_user', {})
    session_id = session.get('session_id')
    
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    # Notify channel
    send_to_channel(f"""🔴 <b>LOGOUT</b>
👤 {user.get('full_name', 'N/A')}
🆔 <code>{user.get('telegram_id', 'N/A')}</code>
⏰ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}""")
    
    session.clear()
    return jsonify(ok=True)

@app.route("/api/user", methods=["GET"])
@login_required
def get_user():
    return jsonify(ok=True, user=session['telegram_user'])

@app.route("/api/sessions", methods=["GET"])
@login_required
def get_sessions():
    """Get all active sessions for this user"""
    tg_id = session['telegram_user']['telegram_id']
    sessions = [s for s in user_sessions.values() if s['telegram_id'] == tg_id]
    return jsonify(ok=True, sessions=sessions)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)