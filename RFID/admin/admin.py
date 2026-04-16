from flask import Blueprint, render_template, jsonify, request
import psycopg2
import serial
import threading
import time
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

admin_bp = Blueprint("admin_bp", __name__, template_folder="template")

latest_scan = {
    "message": "Waiting for scan...",
    "uid": "",
    "name": "",
    "time": ""
}

# =========================
# DB CONNECTION
# =========================

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "RFID"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        port=os.getenv("DB_PORT", "5432")
    )

conn = None
try:
    conn = get_connection()
    conn.autocommit = True
    print("Connected to PostgreSQL successfully.")
except Exception as e:
    print("Database connection error:", e)

def ensure_connection():
    global conn
    try:
        if conn is None or conn.closed != 0:
            conn = get_connection()
            conn.autocommit = True
            print("Reconnected to PostgreSQL.")
    except Exception as e:
        print("Reconnect failed:", e)
        conn = None


# =========================
# SERIAL CONNECTION
# =========================

ser = None
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    print("Serial connected on COM3")
    time.sleep(2)
except Exception as e:
    print("Serial connection error:", e)


# =========================
# HELPER FUNCTIONS
# =========================

def normalize_uid(uid):
    if not uid:
        return ""
    return str(uid).replace(" ", "").replace("-", "").replace(":", "").strip().upper()

def format_uid(uid):
    uid = normalize_uid(uid)
    if len(uid) == 8:
        return f"{uid[0:2]} {uid[2:4]} {uid[4:6]} {uid[6:8]}"
    return uid

def is_valid_uid(uid):
    uid = normalize_uid(uid)
    if not uid:
        return False

    allowed = "0123456789ABCDEF"
    for ch in uid:
        if ch not in allowed:
            return False

    if len(uid) not in [8, 14]:
        return False

    return True


# =========================
# BASIC ROUTES
# =========================

@admin_bp.route('/')
def index():
    return render_template("index.html", data=latest_scan)

@admin_bp.route('/get_latest')
def get_latest():
    return jsonify(latest_scan)

@admin_bp.route('/test_db')
def test_db():
    ensure_connection()
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


# =========================
# RFID LISTEN & SAVE
# =========================

def save_uid_to_db(uid):
    ensure_connection()
    if conn is None:
        return

    uid = normalize_uid(uid)

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id
            FROM rfid_cards
            WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(uid), ' ', ''), '-', ''), ':', '')) = %s
            LIMIT 1;
        """, (uid,))
        if cur.fetchone():
            cur.close()
            return

        cur.execute("INSERT INTO rfid_cards (uid) VALUES (%s);", (uid,))
        cur.close()
        print(f"UID saved: {uid}")
    except Exception as e:
        print("Database insert error:", e)

def get_student_name_by_uid(uid):
    ensure_connection()
    if conn is None:
        return ""

    uid = normalize_uid(uid)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT full_name
            FROM students
            WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) = %s
            LIMIT 1;
        """, (uid,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else ""
    except Exception as e:
        print("get_student_name_by_uid error:", e)
        return ""

def listen():
    global latest_scan
    while True:
        try:
            if ser and ser.is_open and ser.in_waiting > 0:
                raw = ser.readline().decode('utf-8', errors='ignore').strip()

                if not raw or not raw.upper().startswith("UID:"):
                    time.sleep(0.2)
                    continue

                uid = normalize_uid(raw.split(":", 1)[1].strip())
                if not is_valid_uid(uid):
                    continue

                save_uid_to_db(uid)
                student_name = get_student_name_by_uid(uid)

                latest_scan = {
                    "message": "Card scanned successfully",
                    "uid": format_uid(uid),
                    "name": student_name if student_name else "No linked student",
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

        except Exception as e:
            print("Serial read error:", e)

        time.sleep(0.2)

threading.Thread(target=listen, daemon=True).start()


# =========================
# HISTORY PAGE
# =========================

@admin_bp.route('/history')
def history():
    ensure_connection()
    if conn is None:
        return render_template('history.html', history=[])

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                r.id,
                r.uid,
                COALESCE(s.full_name, 'Unregistered Card') AS full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                 = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            ORDER BY r.created_at DESC;
        """)
        rows = cur.fetchall()
        cur.close()

        history_list = [{
            "id": row[0],
            "uid": format_uid(row[1]) if row[1] else "—",
            "full_name": row[2] or "Unregistered Card",
            "timestamp": row[3].strftime("%B %d, %Y  %I:%M %p") if row[3] else "—"
        } for row in rows]

        return render_template('history.html', history=history_list)

    except Exception as e:
        print("History route error:", e)
        return render_template('history.html', history=[])


@admin_bp.route('/history/filter')
def history_filter():
    ensure_connection()
    selected_date = request.args.get('date')

    if not selected_date:
        return jsonify([])

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                r.id,
                r.uid,
                COALESCE(s.full_name, 'Unregistered Card') AS full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                 = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            WHERE DATE(r.created_at) = %s
            ORDER BY r.created_at DESC;
        """, (selected_date,))
        rows = cur.fetchall()
        cur.close()

        result = [{
            "id": row[0],
            "uid": format_uid(row[1]) if row[1] else "—",
            "full_name": row[2] or "Unregistered Card",
            "timestamp": row[3].strftime("%B %d, %Y  %I:%M %p") if row[3] else "—"
        } for row in rows]

        return jsonify(result)

    except Exception as e:
        print("History filter error:", e)
        return jsonify([])