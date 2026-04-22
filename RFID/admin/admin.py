from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash, session
import psycopg2
import psycopg2.extras
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
# DB CONNECTION - SUPABASE VERSION
# =========================

def get_db_connection():
    """Create a new database connection to Supabase"""
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT"),
            sslmode='require'  # Required for Supabase
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def get_connection():
    """Get a database connection (creates new one if needed)"""
    try:
        conn = get_db_connection()
        if conn:
            return conn
        else:
            return None
    except Exception as e:
        print(f"Connection error: {e}")
        return None

# Test connection on startup
print("Testing Supabase connection...")
print(f"Connecting to: {os.getenv('DB_HOST')}")
test_conn = get_connection()
if test_conn:
    print("✓ Supabase connected successfully")
    test_conn.close()
else:
    print("✗ Supabase connection failed! Please check your .env file")

# =========================
# SERIAL CONNECTION
# =========================

ser = None
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    print("Serial connected on COM3")
    time.sleep(2)
except Exception as e:
    print(f"Serial connection error: {e}")


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
    conn = get_connection()
    if conn is None:
        print("Database connection failed in get_all_students")
        return []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
        conn.close()
        return rows
    except Exception as e:
        print(f"get_all_students error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_student_name_by_uid(uid):
    conn = get_connection()
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
        conn.close()

        if not row:
            return ""

        return build_full_name(row[0], row[1], row[2], row[3])

    except Exception as e:
        print(f"get_student_name_by_uid error: {e}")
        return ""
    finally:
        if conn:
            conn.close()

def save_uid_to_db(uid):
    conn = get_connection()
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
            conn.close()
            return

        cur.execute("INSERT INTO rfid_cards (uid) VALUES (%s);", (uid,))
        cur.close()
        conn.close()
        print(f"UID saved: {uid}")
    except Exception as e:
        print(f"Database insert error: {e}")
    finally:
        if conn:
            conn.close()

def save_student_to_db(uid, first_name, middle_name, last_name, extension, birthday, contact_number, email, schedule_text, section_id=None):
    conn = get_connection()
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
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Save student error: {e}")
        raise
    finally:
        if conn:
            conn.close()


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
            print(f"Serial read error: {e}")
        time.sleep(0.2)

# Start RFID listener thread
if ser and ser.is_open:
    threading.Thread(target=listen, daemon=True).start()
    print("RFID listener thread started")
else:
    print("RFID listener not started - no serial connection")


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
    """Test database connection"""
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({
                "status": "error", 
                "message": "Database connection failed. Check your .env file and Supabase credentials"
            })
        
        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        # Test if teachers table exists
        conn2 = get_connection()
        cur2 = conn2.cursor()
        cur2.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'teachers'
            );
        """)
        teachers_exists = cur2.fetchone()[0]
        cur2.close()
        conn2.close()
        
        # Test if teacher_accounts table exists
        conn3 = get_connection()
        cur3 = conn3.cursor()
        cur3.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'teacher_accounts'
            );
        """)
        accounts_exists = cur3.fetchone()[0]
        cur3.close()
        conn3.close()
        
        return jsonify({
            "status": "success",
            "message": "Supabase connected successfully",
            "server_time": str(result[0]),
            "teachers_table_exists": teachers_exists,
            "teacher_accounts_table_exists": accounts_exists
        })
    except Exception as e:
        print(f"Test DB error: {e}")
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
    conn = get_connection()
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
        conn.close()

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
        print(f"Schedules error: {e}")
        return render_template('schedules.html', schedules=[])
    finally:
        if conn:
            conn.close()


@admin_bp.route('/history')
@login_required
def history():
    """History page - shows scan records (one per student per day)"""
    return render_template('history.html')


@admin_bp.route('/history/api')
@login_required
def history_api():
    """API endpoint for real-time history updates - one entry per student per day"""
    conn = get_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database not connected", "history": []})

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
            ORDER BY r.created_at DESC
            LIMIT 500;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        seen_keys = set()
        history_list = []
        
        for row in rows:
            student_id = row[1]
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
        print(f"History API error: {e}")
        return jsonify({"success": False, "message": str(e), "history": []})
    finally:
        if conn:
            conn.close()


@admin_bp.route('/history/filter')
@login_required
def history_filter():
    conn = get_connection()
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
        conn.close()

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
        print(f"History filter error: {e}")
        return jsonify([])
    finally:
        if conn:
            conn.close()


# =========================
# TEACHER DB FUNCTIONS
# =========================

def get_teacher_by_email(email):
    """
    Fetch teacher info + account by email.
    Joins teachers + teacher_accounts tables.
    Returns a dict or None.
    """
    conn = get_connection()
    if conn is None:
        print("No database connection")
        return None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT
                t.id,
                t.teacher_id,
                t.first_name,
                t.middle_name,
                t.last_name,
                t.email,
                t.contact_number,
                t.profile_image,
                t.created_at,
                ta.password,
                ta.id AS account_id
            FROM teachers t
            INNER JOIN teacher_accounts ta ON ta.teacher_id = t.id
            WHERE LOWER(TRIM(t.email)) = LOWER(TRIM(%s))
            LIMIT 1;
        """, (email,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            print(f"No teacher found with email: {email}")
            return None

        full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], None)

        return {
            "id": row['id'],
            "teacher_id": row['teacher_id'],
            "first_name": row['first_name'],
            "middle_name": row['middle_name'],
            "last_name": row['last_name'],
            "full_name": full_name,
            "email": row['email'],
            "contact_number": row['contact_number'],
            "profile_picture": row['profile_image'],
            "created_at": row['created_at'],
            "password_hash": row['password'],
            "account_id": row['account_id'],
        }
    except Exception as e:
        print(f"get_teacher_by_email error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_teacher_by_id(teacher_db_id):
    """
    Fetch teacher info by primary key (teachers.id).
    Returns a dict or None.
    """
    conn = get_connection()
    if conn is None:
        return None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT
                t.id,
                t.teacher_id,
                t.first_name,
                t.middle_name,
                t.last_name,
                t.email,
                t.contact_number,
                t.profile_image,
                t.created_at
            FROM teachers t
            WHERE t.id = %s
            LIMIT 1;
        """, (teacher_db_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return None

        full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], None)

        return {
            "id": row['id'],
            "teacher_id": row['teacher_id'],
            "first_name": row['first_name'],
            "middle_name": row['middle_name'],
            "last_name": row['last_name'],
            "full_name": full_name,
            "email": row['email'],
            "contact_number": row['contact_number'],
            "profile_picture": row['profile_image'],
            "created_at": row['created_at'],
        }
    except Exception as e:
        print(f"get_teacher_by_id error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def update_teacher_in_db(teacher_db_id, fields: dict):
    """
    Update allowed fields in the teachers table.
    """
    conn = get_connection()
    if conn is None:
        raise Exception("Database not connected")

    allowed = {"first_name", "middle_name", "last_name", "contact_number"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        return

    set_clause = ", ".join([f"{col} = %s" for col in updates.keys()])
    values = list(updates.values()) + [teacher_db_id]

    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE teachers SET {set_clause} WHERE id = %s;", values)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"update_teacher_in_db error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def update_teacher_profile_image(teacher_db_id, image_path):
    """Update the profile_image column for a teacher."""
    conn = get_connection()
    if conn is None:
        raise Exception("Database not connected")

    try:
        cur = conn.cursor()
        cur.execute("UPDATE teachers SET profile_image = %s WHERE id = %s;", (image_path, teacher_db_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"update_teacher_profile_image error: {e}")
        raise
    finally:
        if conn:
            conn.close()


# =========================
# TEACHER LOGIN & PROFILE
# =========================

@admin_bp.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')

        print(f"Login attempt for: {identifier}")

        teacher = get_teacher_by_email(identifier)

        if teacher:
            print(f"Teacher found: {teacher['email']}")
            if check_password_hash(teacher['password_hash'], password):
                session.permanent = True
                session['teacher_logged_in'] = True
                session['teacher_id'] = teacher['id']
                session['teacher_email'] = teacher['email']
                flash('Login successful!', 'success')
                return redirect(url_for('admin_bp.teacher_profile'))
            else:
                print("Password mismatch")
                flash('Invalid email or password.', 'error')
        else:
            print("Teacher not found")
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
    data = request.get_json()
    teacher_id = session.get('teacher_id')

    if not teacher_id:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    try:
        fields = {}
        if "first_name" in data:
            fields["first_name"] = data["first_name"]
        if "middle_name" in data:
            fields["middle_name"] = data["middle_name"]
        if "last_name" in data:
            fields["last_name"] = data["last_name"]
        if "contact_number" in data:
            fields["contact_number"] = data["contact_number"]

        update_teacher_in_db(teacher_id, fields)
        return jsonify({"success": True, "message": "Profile updated successfully"})

    except Exception as e:
        print(f"update_teacher_profile error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


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
@login_required
def upload_teacher_profile_pic():
    if 'profile_picture' not in request.files:
        return jsonify({"success": False, "message": "No file provided"})

    file = request.files['profile_picture']
    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected"})

    if file:
        teacher_id = session.get('teacher_id')
        filename = secure_filename(f"teacher_{teacher_id}_{file.filename}")
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)

        image_url = f"/static/uploads/teachers/{filename}"

        try:
            update_teacher_profile_image(teacher_id, image_url)
        except Exception as e:
            return jsonify({"success": False, "message": f"File saved but DB update failed: {e}"})

        return jsonify({"success": True, "image_url": image_url})

    return jsonify({"success": False, "message": "Upload failed"})