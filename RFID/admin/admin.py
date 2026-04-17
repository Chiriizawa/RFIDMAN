from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash, session
import psycopg2
import serial
import threading
import time
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

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

def build_full_name(first_name, middle_name, last_name, extension):
    parts = [
        str(first_name or "").strip(),
        str(middle_name or "").strip(),
        str(last_name or "").strip(),
        str(extension or "").strip()
    ]
    return " ".join([p for p in parts if p])

def get_all_students():
    ensure_connection()
    if conn is None:
        print("Database connection failed in get_all_students")
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                id,
                uid,
                first_name,
                middle_name,
                last_name,
                extension,
                birthday,
                contact_number,
                email,
                schedule,
                section_id,
                created_at
            FROM students
            ORDER BY last_name ASC, first_name ASC, middle_name ASC;
        """)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        print("get_all_students error:", e)
        return []

def get_student_name_by_uid(uid):
    ensure_connection()
    if conn is None:
        return ""

    uid = normalize_uid(uid)

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT first_name, middle_name, last_name, extension
            FROM students
            WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) = %s
            LIMIT 1;
        """, (uid,))
        row = cur.fetchone()
        cur.close()

        if not row:
            return ""

        return build_full_name(row[0], row[1], row[2], row[3])

    except Exception as e:
        print("get_student_name_by_uid error:", e)
        return ""

def save_uid_to_db(uid):
    ensure_connection()
    if conn is None:
        return

    uid = normalize_uid(uid)

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM rfid_cards
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

def save_student_to_db(uid, first_name, middle_name, last_name, extension, birthday, contact_number, email, schedule_text, section_id=None):
    ensure_connection()
    if conn is None:
        raise Exception("Database not connected")

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO students (
                uid,
                first_name,
                middle_name,
                last_name,
                extension,
                birthday,
                contact_number,
                email,
                schedule,
                section_id,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            uid,
            first_name,
            middle_name,
            last_name,
            extension,
            birthday,
            contact_number,
            email,
            schedule_text,
            section_id
        ))
        cur.close()
    except Exception as e:
        print("Save student error:", e)
        raise


# =========================
# LOGIN REQUIRED DECORATOR
# =========================
def login_required(f):
    def decorated_function(*args, **kwargs):
        if not session.get('teacher_logged_in'):
            flash('Please login first.', 'error')
            return redirect(url_for('admin_bp.teacher_login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


# =========================
# RFID LISTEN & SAVE (Background Thread)
# =========================

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
# BASIC ROUTES
# =========================

@admin_bp.route('/')
@admin_bp.route('/admin/')
def index():
    """Main RFID Dashboard"""
    if not session.get('teacher_logged_in'):
        return redirect(url_for('admin_bp.teacher_login'))

    return render_template('dashboard.html')


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
# PROTECTED ROUTES
# =========================

@admin_bp.route('/registered_students')
@login_required
def registered_students():
    students = get_all_students()
    student_list = []

    for row in students:
        full_name = build_full_name(row[2], row[3], row[4], row[5])

        student_list.append({
            "id": row[0],
            "uid": format_uid(row[1]) if row[1] else "—",
            "first_name": row[2] or "—",
            "middle_name": row[3] or "—",
            "last_name": row[4] or "—",
            "extension": row[5] or "—",
            "full_name": full_name if full_name else "—",
            "birthday": str(row[6]) if row[6] else "—",
            "contact_number": row[7] or "—",
            "email": row[8] or "—",
            "schedule": row[9] or "—",
            "section_id": row[10],
            "created_at": row[11].strftime("%b %d, %Y  %I:%M %p") if row[11] else "—"
        })

    return render_template('registered_students.html', students=student_list)


@admin_bp.route('/schedules')
@login_required
def schedules():
    ensure_connection()
    if conn is None:
        return render_template('schedules.html', schedules=[])

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                uid,
                first_name,
                middle_name,
                last_name,
                extension,
                birthday,
                contact_number,
                email,
                schedule,
                section_id,
                created_at
            FROM students
            WHERE schedule IS NOT NULL AND TRIM(schedule) != ''
            ORDER BY last_name ASC, first_name ASC, middle_name ASC;
        """)
        rows = cur.fetchall()
        cur.close()

        schedule_list = []
        for row in rows:
            full_name = build_full_name(row[2], row[3], row[4], row[5])

            schedule_list.append({
                "id": row[0],
                "uid": format_uid(row[1]) if row[1] else "—",
                "first_name": row[2] or "—",
                "middle_name": row[3] or "—",
                "last_name": row[4] or "—",
                "extension": row[5] or "—",
                "full_name": full_name if full_name else "—",
                "birthday": str(row[6]) if row[6] else "—",
                "contact_number": row[7] or "—",
                "email": row[8] or "—",
                "schedule": row[9] or "—",
                "section_id": row[10],
                "created_at": row[11].strftime("%b %d, %Y  %I:%M %p") if row[11] else "—"
            })

        return render_template('schedules.html', schedules=schedule_list)
    except Exception as e:
        print("Schedules error:", e)
        return render_template('schedules.html', schedules=[])


@admin_bp.route('/history')
@login_required
def history():
    """History page - shows scan records (one per student per day)"""
    return render_template('history.html')


@admin_bp.route('/history/api')
@login_required
def history_api():
    """API endpoint for real-time history updates - one entry per student per day"""
    ensure_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database not connected", "history": []})

    try:
        cur = conn.cursor()
        # Get all scans with student names, ordered by most recent first
        cur.execute("""
            SELECT 
                r.id,
                r.uid,
                COALESCE(
                    NULLIF(
                        TRIM(CONCAT(
                            COALESCE(s.first_name, ''), ' ',
                            COALESCE(s.middle_name, ''), ' ',
                            COALESCE(s.last_name, ''), ' ',
                            COALESCE(s.extension, '')
                        )),
                        ''
                    ),
                    'Unregistered Card'
                ) AS full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                 = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            ORDER BY r.created_at DESC
            LIMIT 500;
        """)
        rows = cur.fetchall()
        cur.close()

        # Process results and filter to one per student per day
        seen_keys = set()
        history_list = []
        
        for row in rows:
            # Create a key combining student identifier and date
            student_id = row[1]  # UID
            scan_date = row[3].date() if row[3] else None
            
            if scan_date:
                key = f"{student_id}_{scan_date}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    history_list.append({
                        "id": row[0],
                        "uid": format_uid(row[1]) if row[1] else "—",
                        "full_name": row[2] or "Unregistered Card",
                        "scan_date": scan_date.strftime("%B %d, %Y") if scan_date else "—",
                        "scan_time": row[3].strftime("%I:%M:%S %p") if row[3] else "—"
                    })

        return jsonify({"success": True, "history": history_list})

    except Exception as e:
        print("History API error:", e)
        return jsonify({"success": False, "message": str(e), "history": []})


@admin_bp.route('/history/filter')
@login_required
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
                COALESCE(
                    NULLIF(
                        TRIM(CONCAT(
                            COALESCE(s.first_name, ''), ' ',
                            COALESCE(s.middle_name, ''), ' ',
                            COALESCE(s.last_name, ''), ' ',
                            COALESCE(s.extension, '')
                        )),
                        ''
                    ),
                    'Unregistered Card'
                ) AS full_name,
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

        # Filter to one per student per day
        seen_keys = set()
        result = []
        
        for row in rows:
            student_id = row[1]
            scan_date = row[3].date() if row[3] else None
            
            if scan_date:
                key = f"{student_id}_{scan_date}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    result.append({
                        "id": row[0],
                        "uid": format_uid(row[1]) if row[1] else "—",
                        "full_name": row[2] or "Unregistered Card",
                        "scan_date": scan_date.strftime("%B %d, %Y") if scan_date else "—",
                        "scan_time": row[3].strftime("%I:%M:%S %p") if row[3] else "—"
                    })

        return jsonify(result)
    except Exception as e:
        print("History filter error:", e)
        return jsonify([])


# =========================
# TEACHER LOGIN & PROFILE
# =========================

TEACHERS = {
    "teacher@tapandknow.com": {
        "id": 1,
        "full_name": "Mr. Juan Dela Cruz",
        "email": "teacher@tapandknow.com",
        "password_hash": generate_password_hash("teacher123"),
        "subject": "Mathematics",
        "contact_number": "09123456789",
        "department": "STEM",
        "bio": "Senior Mathematics teacher with 12 years experience.",
        "profile_picture": None,
        "created_at": datetime.now()
    }
}

def get_teacher_by_identifier(identifier):
    identifier = str(identifier).lower().strip()
    for email, data in TEACHERS.items():
        if email.lower() == identifier:
            return data
    return None

def get_teacher_by_id(teacher_id):
    for data in TEACHERS.values():
        if data.get("id") == teacher_id:
            return data
    return None


@admin_bp.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')

        teacher = get_teacher_by_identifier(identifier)
        if teacher and check_password_hash(teacher['password_hash'], password):
            session.permanent = True
            session['teacher_logged_in'] = True
            session['teacher_id'] = teacher['id']
            session['teacher_email'] = teacher['email']
            flash('Login successful!', 'success')
            return redirect(url_for('admin_bp.teacher_profile'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')


@admin_bp.route('/teacher/profile')
@login_required
def teacher_profile():
    teacher = get_teacher_by_id(session.get('teacher_id'))
    if not teacher:
        session.clear()
        flash('Session expired.', 'error')
        return redirect(url_for('admin_bp.teacher_login'))

    return render_template('teacher_profile.html', current_teacher=teacher)


@admin_bp.route('/teacher/update_profile', methods=['POST'])
@login_required
def update_teacher_profile():
    if not session.get('teacher_logged_in'):
        return jsonify({"success": False, "message": "Not logged in"}), 401

    data = request.get_json()
    teacher_id = session.get('teacher_id')

    for email, teacher in TEACHERS.items():
        if teacher.get("id") == teacher_id:
            if "full_name" in data:
                teacher["full_name"] = data["full_name"]
            if "subject" in data:
                teacher["subject"] = data["subject"]
            if "department" in data:
                teacher["department"] = data["department"]
            if "contact_number" in data:
                teacher["contact_number"] = data["contact_number"]
            if "bio" in data:
                teacher["bio"] = data["bio"]
            break

    return jsonify({"success": True, "message": "Profile updated successfully"})


@admin_bp.route('/teacher/logout')
def teacher_logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('admin_bp.teacher_login'))


# =========================
# PROFILE PICTURE UPLOAD
# =========================
UPLOAD_FOLDER = 'static/uploads/teachers'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@admin_bp.route('/teacher/upload_profile_pic', methods=['POST'])
def upload_teacher_profile_pic():
    if not session.get('teacher_logged_in'):
        return jsonify({"success": False, "message": "Not logged in"})

    if 'profile_picture' not in request.files:
        return jsonify({"success": False, "message": "No file"})

    file = request.files['profile_picture']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"})

    if file:
        filename = secure_filename(f"teacher_{session.get('teacher_id')}_{file.filename}")
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)

        teacher_id = session.get('teacher_id')
        for data in TEACHERS.values():
            if data["id"] == teacher_id:
                data["profile_picture"] = f"/static/uploads/teachers/{filename}"
                break

        return jsonify({"success": True, "image_url": f"/static/uploads/teachers/{filename}"})

    return jsonify({"success": False, "message": "Upload failed"})