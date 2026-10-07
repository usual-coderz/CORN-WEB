import os
from flask import Blueprint, render_template, request, jsonify, send_from_directory
from datetime import datetime
from bson import ObjectId
from werkzeug.utils import secure_filename
from config import UPLOAD_FOLDER
from Nexa.database import (
    get_ads_config, update_ads_config, get_stats, get_all_users,
    users_col, broadcast_msgs_col, allowed_file
)
from Nexa.broadcaster import ads_broadcaster
from Nexa.session_manager import session_manager, session_cleaner

admin_bp = Blueprint('admin', __name__, template_folder='../templates')

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

    if update_ads_config({"ads_enabled": new_status}):
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
    return jsonify({"success": False, "error": "Failed"}), 500

@admin_bp.route("/api/set-interval", methods=["POST"])
def set_interval():
    data = request.get_json() or {}
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
    return jsonify({"success": False, "error": "Failed"}), 500

@admin_bp.route("/api/set-caption", methods=["POST"])
def set_caption():
    data = request.get_json() or {}
    caption = data.get("caption", "")
    if update_ads_config({"caption": caption}):
        return jsonify({"success": True, "message": "Caption updated!"})
    return jsonify({"success": False, "error": "Failed"}), 500

@admin_bp.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    if 'photo' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400
    file = request.files['photo']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(f"ad_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file.filename.rsplit('.', 1)[1]}")
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        update_ads_config({"photo_path": filepath})
        return jsonify({"success": True, "path": filepath, "message": "Photo uploaded!"})
    return jsonify({"success": False, "error": "Invalid file type"}), 400

@admin_bp.route("/api/preview")
def preview_ad():
    config = get_ads_config()
    return jsonify({
        "success": True,
        "photo_path": config.get("photo_path"),
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
        user = users_col.find_one({"_id": ObjectId(user_id)})
    except:
        return jsonify({"success": False, "error": "Invalid user ID"}), 400
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    new_status = not user.get("ads_enabled", True)
    users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"ads_enabled": new_status}})
    return jsonify({"success": True, "enabled": new_status, "message": f"User ads {'enabled' if new_status else 'disabled'}"})

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