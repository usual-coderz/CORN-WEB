from flask import Flask, render_template, request, session, redirect, jsonify
from bot import send_log
import random, time

app = Flask(__name__)
app.secret_key = "9fK2vXq7Lp4mWz8RjB3nYc6TdHs1Ea5Ug0Ox7VwZiMkNr2Cy4PbA"

# Fake "robot check" state
verified = set()

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    return render_template("index.html", user=session["user"])

# ---- Captcha (I'm not a robot style) ----
@app.route("/api/captcha", methods=["POST"])
def captcha():
    # simple slider/checkbox proof-of-human token
    token = request.json.get("token")
    if token == "human-verified":   # replace with real captcha (hCaptcha/Turnstile)
        verified.add(request.remote_addr)
        return jsonify(ok=True)
    return jsonify(ok=False), 400

# ---- Telegram OTP Login ----
@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    phone = request.json["phone"]
    otp = str(random.randint(10000, 99999))
    session[f"otp_{phone}"] = otp
    # Yaha real Telegram bot se OTP bhejo us phone par (bot.py extend karo)
    print(f"OTP for +{phone}: {otp}")   # demo
    return jsonify(ok=True)

@app.route("/api/verify-otp", methods=["POST"])
def verify():
    phone, name = request.json["phone"], request.json.get("name", "Student")
    if session.get(f"otp_{phone}") == request.json["otp"]:
        session["user"] = {"name": name, "phone": phone,
                           "tg_id": request.json.get("tg_id", "N/A")}
        session["login_time"] = time.time()
        send_log(name, phone, request.json.get("tg_id", "N/A"))
        return jsonify(ok=True)
    return jsonify(ok=False), 401

# ---- Logout detection ----
@app.route("/logout")
def logout():
    user = session.get("user")
    session.clear()
    if user:
        send_log(f"LOGOUT: {user['name']}", user["phone"], user.get("tg_id", "N/A"))
    return redirect("/login?msg=Study par focus karo! Wapas login karo.")

# Auto-logout tracker (frontend pings this)
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    session["last_seen"] = time.time()
    return jsonify(ok=True)