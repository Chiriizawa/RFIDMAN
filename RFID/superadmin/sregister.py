from flask import Blueprint, render_template
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

sregister = Blueprint("sregister", __name__, template_folder="template")

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
except Exception as e:
    print("Database connection error:", e)

def ensure_connection():
    global conn
    try:
        if conn is None or conn.closed != 0:
            conn = get_connection()
            conn.autocommit = True
    except Exception as e:
        print("Reconnect failed:", e)
        conn = None

def normalize_uid(uid):
    if not uid:
        return ""
    return str(uid).replace(" ", "").replace("-", "").replace(":", "").strip().upper()

def format_uid(uid):
    uid = normalize_uid(uid)
    if len(uid) == 8:
        return f"{uid[0:2]} {uid[2:4]} {uid[4:6]} {uid[6:8]}"
    return uid

def get_all_students():
    ensure_connection()
    if conn is None:
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, uid, full_name, birthday, contact_number, email, schedule, created_at
            FROM students
            ORDER BY id DESC
        """)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        print("get_all_students error:", e)
        return []

@sregister.route('/student')
def student():
    students_raw = get_all_students()

    students = []
    for row in students_raw:
        students.append({
            "id": row[0],
            "uid": format_uid(row[1]) if row[1] else "No UID",
            "full_name": row[2] if row[2] else "",
            "birthday": row[3].strftime("%Y-%m-%d") if row[3] else "",
            "contact_number": row[4] if row[4] else "",
            "email": row[5] if row[5] else "",
            "schedule": row[6] if row[6] else "",
            "created_at": row[7].strftime("%Y-%m-%d %H:%M:%S") if row[7] else ""
        })

    return render_template("superadmin/student.html", students=students)