from flask import Blueprint, render_template, jsonify
import psycopg2
import serial
import threading
import time
from datetime import datetime

admin_bp = Blueprint("admin_bp", __name__, template_folder="template")

latest_scan = {
    "message": "Waiting for scan...",
    "uid": "",
    "time": ""
}

conn = None
try:
    conn = psycopg2.connect(
        host="localhost",
        database="RFID",
        user="postgres",
        password="12345678",
        port="5432"
    )
    conn.autocommit = True
    print("Connected to PostgreSQL successfully.")
except Exception as e:
    print("Database connection error:", e)

ser = None
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    print("Serial connected on COM3")
    time.sleep(2)
except Exception as e:
    print("Serial connection error:", e)


@admin_bp.route('/')
def index():
    return render_template("index.html", data=latest_scan)


@admin_bp.route('/get_latest')
def get_latest():
    return jsonify(latest_scan)


@admin_bp.route('/get_all_uids')
def get_all_uids():
    if conn is None:
        return jsonify([])
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, uid, created_at FROM rfid_cards ORDER BY created_at DESC;")
        rows = cur.fetchall()
        cur.close()
        return jsonify([
            {
                "id": row[0],
                "uid": row[1],
                "created_at": row[2].strftime("%Y-%m-%d %H:%M:%S")
            }
            for row in rows
        ])
    except Exception as e:
        return jsonify([])


@admin_bp.route('/test_db')
def test_db():
    if conn is None:
        return jsonify({"status": "error", "message": "Database not connected"})
    try:
        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        result = cur.fetchone()
        cur.close()
        return jsonify({
            "status": "success",
            "message": "Database connected successfully",
            "server_time": str(result[0])
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


def save_uid_to_db(uid):
    if conn is None:
        print("No database connection.")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO rfid_cards (uid) VALUES (%s) ON CONFLICT (uid) DO NOTHING;",
            (uid,)
        )
        cur.close()
        print(f"UID saved to database: {uid}")
    except Exception as e:
        print("Database insert error:", e)


def listen():
    global latest_scan
    while True:
        try:
            if ser and ser.is_open:
                if ser.in_waiting > 0:
                    raw = ser.readline().decode('utf-8', errors='ignore').strip()
                    print("RAW SERIAL:", repr(raw))

                    if raw.startswith("UID:"):
                        uid = raw.replace("UID:", "").strip()
                        print("Scanned UID:", uid)
                        latest_scan = {
                            "message": "Card scanned successfully",
                            "uid": uid,
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        save_uid_to_db(uid)
        except Exception as e:
            print("Serial read error:", e)
        time.sleep(0.2)


threading.Thread(target=listen, daemon=True).start()