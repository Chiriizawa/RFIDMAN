from flask import Blueprint, request, jsonify, render_template
import psycopg2
import serial
import threading
import time
import os

rfid_bp = Blueprint("rfid_bp", __name__, template_folder="template")

# ================= SUPABASE DB =================
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432),
        sslmode="require",
        connect_timeout=10
    )

# ================= GLOBAL UID =================
latest_uid = "Wala pa"

# ================= RFID LOOP =================
def rfid_listener():
    global latest_uid

    print("🚀 RFID THREAD STARTED")

    # wait para hindi mag COM lock agad
    time.sleep(2)

    ser = None

    try:
        ser = serial.Serial("COM3", 9600, timeout=1)
        print("🔥 COM3 CONNECTED")

    except Exception as e:
        print("❌ SERIAL ERROR:", e)
        return

    while True:
        try:
            uid = ser.readline().decode(errors='ignore').strip()

            if uid:
                print("📡 RFID:", uid)
                latest_uid = uid

                try:
                    conn = get_db_connection()
                    cur = conn.cursor()

                    cur.execute("""
                        INSERT INTO rfid_cards (uid, created_at)
                        VALUES (%s, NOW())
                    """, (uid,))

                    conn.commit()
                    cur.close()
                    conn.close()

                    print("💾 SAVED TO SUPABASE")

                except Exception as db_error:
                    print("❌ DB ERROR:", db_error)

            time.sleep(0.2)

        except Exception as loop_error:
            print("❌ LOOP ERROR:", loop_error)

# ================= SAFE START =================
def start_rfid_thread():
    thread = threading.Thread(target=rfid_listener, daemon=True)
    thread.start()
    print("🔥 RFID THREAD INITIALIZED")

# ================= ROUTES =================

@rfid_bp.route("/")
def index():
    return render_template("user/index.html")

@rfid_bp.route("/get_uid")
def get_uid():
    return jsonify({"uid": latest_uid})