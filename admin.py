from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask import send_from_directory
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from datetime import datetime, timedelta
import os
import json
from werkzeug.utils import secure_filename

app = Flask(__name__)  # FIXED: was Flask(name)
app.secret_key = os.environ.get("ADMIN_SECRET_KEY", "admin-secret-key-12345")

# ========== MONGODB CONFIG ==========
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://nexacoders2_db_user:dxYh7QOdHvH6OVdd@cluster0.f4qxcbk.mongodb.net/?appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "adult_bot_db")

try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    mongo_client.admin.command('ping')
    print("✅ MongoDB Connected!")
    db_connected = True
except:
    print("❌ MongoDB Not Connected")
    db = None
    db_connected = False

users_col = db.users if db else None
ads_config_col = db.ads_config if db else None
ads_logs_col = db.ads_logs if db else None

# ========== UPLOAD CONFIG ==========
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ========== DATABASE HELPERS ==========
def get_ads_config():
    """Get or create ads configuration"""
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
    """Update ads configuration"""
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
    """Get all statistics"""
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

    # Get total ads sent
    pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$total_ads_sent"}}}
    ]
    ads_result = list(users_col.aggregate(pipeline))
    total_ads = ads_result[0]["total"] if ads_result else 0

    # Get recent users (last 24 hours)
    yesterday = datetime.now() - timedelta(days=1)
    recent_users = users_col.count_documents({"created_at": {"$gte": yesterday}})

    # Get online users (active in last hour)
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
    """Get all users with details"""
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
            "created_at": user.get("created_at", datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "last_ad_time": user.get("last_ad_time", "").strftime("%Y-%m-%d %H:%M") if user.get("last_ad_time") else "Never"
        })

    return formatted

# ========== ROUTES ==========
@app.route("/")
def index():
    return redirect(url_for('admin_panel'))

@app.route("/admin")
def admin_panel():
    """Main admin panel"""
    config = get_ads_config()
    stats = get_stats()
    return render_template("admin.html", config=config, stats=stats)

@app.route("/admin/stats")
def admin_stats_page():
    """Stats page"""
    stats = get_stats()
    users = get_all_users()
    config = get_ads_config()
    return render_template("stats.html", stats=stats, users=users, config=config)

@app.route("/api/toggle-ads", methods=["POST"])
def toggle_ads():
    """Toggle ads on/off"""
    config = get_ads_config()
    new_status = not config.get("ads_enabled", False)

    if update_ads_config({"ads_enabled": new_status}):
        # Also update all users
        if users_col:
            users_col.update_many({}, {"$set": {"ads_enabled": new_status}})

        return jsonify({
            "success": True,
            "enabled": new_status,
            "message": f"Ads {'enabled' if new_status else 'disabled'} successfully!"
        })

    return jsonify({"success": False, "error": "Failed to update"}), 500

@app.route("/api/set-interval", methods=["POST"])
def set_interval():
    """Set ads interval"""
    interval = request.json.get("interval")

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
    """Set ads caption"""
    caption = request.json.get("caption", "")

    if update_ads_config({"caption": caption}):
        return jsonify({
            "success": True,
            "message": "Caption updated successfully!"
        })

    return jsonify({"success": False, "error": "Failed to update"}), 500

@app.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    """Upload photo for ads"""
    if 'photo' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(f"ad_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file.filename.rsplit('.', 1)[1]}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Update config with new photo path
        update_ads_config({"photo_path": filepath})

        return jsonify({
            "success": True,
            "path": filepath,
            "message": "Photo uploaded successfully!"
        })

    return jsonify({"success": False, "error": "Invalid file type"}), 400

@app.route("/api/preview")
def preview_ad():
    """Get ad preview data"""
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
    """Serve uploaded files"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/api/user/<user_id>/toggle", methods=["POST"])
def toggle_user_ads(user_id):
    """Toggle ads for specific user"""
    if users_col is None:
        return jsonify({"success": False, "error": "DB not connected"}), 500

    from bson.objectid import ObjectId
    user = users_col.find_one({"_id": ObjectId(user_id)})

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
    """Get fresh stats"""
    return jsonify(get_stats())

# FIXED: was if name == "main":
if __name__ == "__main__":
    port = int(os.environ.get("ADMIN_PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)