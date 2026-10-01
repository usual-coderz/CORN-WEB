from flask import Flask, render_template, request, session, redirect, jsonify
import random, time, logging

app = Flask(__name__)
app.secret_key = "9fK2vXq7Lp4mWz8RjB3nYc6TdHs1Ea5Ug0Ox7VwZiMkNr2Cy4PbA"

logging.basicConfig(level=logging.INFO)

def send_log(event, tg_id="N/A"):
    logging.info("EVENT=%s TG_ID=%s", event, tg_id)

verified = set()

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    return render_template("index.html", user=session["user"])

@app.route("/api/captcha", methods=["POST"])
def captcha():
    token = request.json.get("token")
    if token == "human-verified":
        verified.add(request.remote_addr)
        return jsonify(ok=True)
    return jsonify(ok=False), 400

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    phone = request.json["phone"]
    otp = str(random.randint(10000, 99999))
    session[f"otp_{phone}"] = otp
    print(f"OTP generated for verification")
    return jsonify(ok=True)

@app.route("/api/verify-otp", methods=["POST"])
def verify():
    data = request.json
    phone = data["phone"]
    name = data.get("name", "Student")
    tg_id = data.get("tg_id", "N/A")

    if session.get(f"otp_{phone}") == data["otp"]:
        session["user"] = {
            "name": name,
            "tg_id": tg_id
        }
        session["login_time"] = time.time()

        send_log("LOGIN_SUCCESS", tg_id)

        return jsonify(ok=True)

    send_log("LOGIN_FAILED", tg_id)
    return jsonify(ok=False), 401

@app.route("/logout")
def logout():
    user = session.get("user")

    if user:
        send_log("LOGOUT", user.get("tg_id", "N/A"))

    session.clear()
    return redirect("/login")

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    session["last_seen"] = time.time()
    return jsonify(ok=True)