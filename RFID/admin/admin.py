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
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise Exception("❌ DATABASE_URL not found in .env")
    return psycopg2.connect(
        database_url.strip(),
        sslmode="require"
    )

def get_connection():
    try:
        conn = get_db_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        return None
    except Exception as e:
        print(f"Connection error: {e}")
        return None

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
        return []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT 
                s.id,
                s.uid,
                s.first_name,
                s.middle_name,
                s.last_name,
                s.extension,
                s.contact_number,
                s.email,
                s.schedule,
                s.section_id,
                s.created_at,
                sec.section_name,
                sec.year_level
            FROM students s
            LEFT JOIN sections sec ON s.section_id = sec.id
            ORDER BY s.last_name ASC, s.first_name ASC, s.middle_name ASC;
        """)
        rows = cur.fetchall()
        cur.close()
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
            return
        cur.execute("INSERT INTO rfid_cards (uid) VALUES (%s);", (uid,))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Database insert error: {e}")
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
# RFID LISTEN THREAD
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
    if not session.get('teacher_logged_in'):
        return redirect(url_for('admin_bp.teacher_login'))
    return render_template('dashboard.html')

@admin_bp.route('/get_latest')
def get_latest():
    return jsonify(latest_scan)

@admin_bp.route('/test_db')
def test_db():
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({"status": "error", "message": "Database connection failed"})
        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "Connected", "server_time": str(result[0])})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# =========================
# SECTIONS MANAGEMENT
# =========================
@admin_bp.route('/sections')
@login_required
def manage_sections():
    conn = get_connection()
    teachers = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT id, first_name, last_name, email FROM teachers ORDER BY last_name, first_name")
            teachers = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"Error fetching teachers: {e}")
    return render_template('manage_sections.html', teachers=teachers)

@admin_bp.route('/sections/api')
@login_required
def sections_api():
    conn = get_connection()
    if conn is None:
        return jsonify({"success": False, "sections": []})
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT 
                s.id, s.section_name, s.year_level, s.teacher_id, s.created_at,
                t.first_name, t.last_name, t.email as teacher_email,
                COUNT(st.id) as student_count
            FROM sections s
            LEFT JOIN teachers t ON t.id = s.teacher_id
            LEFT JOIN students st ON st.section_id = s.id
            GROUP BY s.id, t.id, t.first_name, t.last_name, t.email
            ORDER BY s.section_name
        """)
        sections = cur.fetchall()
        cur.close()
        conn.close()
        sections_list = []
        for section in sections:
            sections_list.append({
                "id": section["id"],
                "section_name": section["section_name"],
                "year_level": section["year_level"],
                "teacher_id": section["teacher_id"],
                "first_name": section["first_name"],
                "last_name": section["last_name"],
                "teacher_email": section["teacher_email"],
                "student_count": section["student_count"] or 0,
                "created_at": section["created_at"].strftime("%Y-%m-%d %H:%M:%S") if section["created_at"] else None
            })
        return jsonify({"success": True, "sections": sections_list})
    except Exception as e:
        print(f"Sections API error: {e}")
        return jsonify({"success": False, "sections": []})
    finally:
        if conn:
            conn.close()

@admin_bp.route('/sections/add', methods=['POST'])
@login_required
def add_section():
    try:
        data = request.get_json()
        section_name = data.get('section_name', '').strip()
        year_level = data.get('year_level', '').strip()
        teacher_id = data.get('teacher_id') or None
        if not section_name:
            return jsonify({"success": False, "message": "Section name is required"}), 400
        conn = get_connection()
        if conn is None:
            return jsonify({"success": False, "message": "Database connection failed"}), 500
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sections (section_name, year_level, teacher_id, created_at)
            VALUES (%s, %s, %s, NOW()) RETURNING id
        """, (section_name, year_level, teacher_id))
        section_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Section added successfully", "section_id": section_id})
    except Exception as e:
        print(f"Add section error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_bp.route('/sections/update/<int:section_id>', methods=['PUT'])
@login_required
def update_section(section_id):
    try:
        data = request.get_json()
        section_name = data.get('section_name', '').strip()
        year_level = data.get('year_level', '').strip()
        teacher_id = data.get('teacher_id') or None
        if not section_name:
            return jsonify({"success": False, "message": "Section name is required"}), 400
        conn = get_connection()
        if conn is None:
            return jsonify({"success": False, "message": "Database connection failed"}), 500
        cur = conn.cursor()
        cur.execute("""
            UPDATE sections SET section_name = %s, year_level = %s, teacher_id = %s
            WHERE id = %s RETURNING id
        """, (section_name, year_level, teacher_id, section_id))
        if cur.rowcount == 0:
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Section not found"}), 404
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Section updated successfully"})
    except Exception as e:
        print(f"Update section error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_bp.route('/sections/delete/<int:section_id>', methods=['DELETE'])
@login_required
def delete_section(section_id):
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({"success": False, "message": "Database connection failed"}), 500
        cur = conn.cursor()
        cur.execute("SELECT id FROM sections WHERE id = %s", (section_id,))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Section not found"}), 404
        cur.execute("UPDATE students SET section_id = NULL WHERE section_id = %s", (section_id,))
        cur.execute("DELETE FROM sections WHERE id = %s", (section_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Section deleted successfully"})
    except Exception as e:
        print(f"Delete section error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_bp.route('/api/get_sections_for_sidebar')
@login_required
def api_get_sections_for_sidebar():
    conn = get_connection()
    if conn is None:
        return jsonify({'success': False, 'sections': []})
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT s.id, s.section_name, s.year_level, COUNT(st.id) as student_count
            FROM sections s
            LEFT JOIN students st ON st.section_id = s.id
            GROUP BY s.id
            ORDER BY s.section_name
        """)
        sections = cur.fetchall()
        cur.close()
        conn.close()
        section_list = []
        for section in sections:
            section_list.append({
                'id': section['id'],
                'section_name': section['section_name'],
                'year_level': section['year_level'],
                'student_count': section['student_count'] or 0
            })
        return jsonify({'success': True, 'sections': section_list})
    except Exception as e:
        print(f"API get sections error: {e}")
        if conn:
            conn.close()
        return jsonify({'success': False, 'sections': []})

@admin_bp.route('/class_section/<section_id>')
@login_required
def class_section(section_id):
    conn = get_connection()
    if conn is None:
        flash('Database connection error', 'error')
        return render_template('class_dashboard.html', section=section_id, section_info={}, students=[])
    try:
        is_numeric = section_id.isdigit()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if is_numeric:
            cur.execute("""
                SELECT s.*, t.first_name as teacher_first_name, t.last_name as teacher_last_name, t.email as teacher_email
                FROM sections s LEFT JOIN teachers t ON t.id = s.teacher_id WHERE s.id = %s
            """, (int(section_id),))
        else:
            cur.execute("""
                SELECT s.*, t.first_name as teacher_first_name, t.last_name as teacher_last_name, t.email as teacher_email
                FROM sections s LEFT JOIN teachers t ON t.id = s.teacher_id WHERE s.section_name = %s
            """, (section_id,))
        section = cur.fetchone()
        if not section:
            flash('Section not found', 'error')
            return redirect(url_for('admin_bp.manage_sections'))
        cur.execute("""
            SELECT id, uid, first_name, middle_name, last_name, extension, contact_number, email, schedule, section_id, created_at
            FROM students WHERE section_id = %s ORDER BY last_name ASC, first_name ASC
        """, (section['id'],))
        rows = cur.fetchall()
        cur.close()
        student_list = []
        today = datetime.now().strftime('%Y-%m-%d')
        for row in rows:
            full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], row['extension'])
            cur2 = conn.cursor()
            cur2.execute("""
                SELECT created_at FROM rfid_cards 
                WHERE DATE(created_at) = %s 
                AND UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) 
                = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(%s, '')), ' ', ''), '-', ''), ':', ''))
                LIMIT 1
            """, (today, row['uid']))
            attendance = cur2.fetchone()
            cur2.close()
            student_list.append({
                "id": row['id'],
                "uid": format_uid(row['uid']) if row['uid'] else "—",
                "full_name": full_name if full_name else "—",
                "first_name": row['first_name'] or "—",
                "middle_name": row['middle_name'] or "—",
                "last_name": row['last_name'] or "—",
                "extension": row['extension'] or "—",
                "contact_number": row['contact_number'] or "—",
                "email": row['email'] or "—",
                "schedule": row['schedule'] or "—",
                "present_today": attendance is not None,
                "scan_time": attendance[0].strftime("%I:%M:%S %p") if attendance else "—"
            })
        conn.close()
        adviser_name = ""
        if section['teacher_first_name'] and section['teacher_last_name']:
            adviser_name = f"{section['teacher_first_name']} {section['teacher_last_name']}"
        elif section['teacher_first_name']:
            adviser_name = section['teacher_first_name']
        elif section['teacher_last_name']:
            adviser_name = section['teacher_last_name']
        else:
            adviser_name = "Not Assigned"
        section_info = {
            'name': section['section_name'],
            'adviser': adviser_name,
            'room': "TBD",
            'total_students': len(student_list),
            'present_today': sum(1 for s in student_list if s['present_today'])
        }
        return render_template('class_dashboard.html', section=section['id'], section_info=section_info, students=student_list)
    except Exception as e:
        print(f"Class section error: {e}")
        flash(f'Error loading class section: {str(e)}', 'error')
        if conn:
            conn.close()
        return render_template('class_dashboard.html', section=section_id, section_info={'name': 'Error', 'adviser': 'Error', 'room': 'Error', 'total_students': 0, 'present_today': 0}, students=[])

# =========================
# STUDENT MANAGEMENT
# =========================
@admin_bp.route('/registered_students')
@login_required
def registered_students():
    students = get_all_students()
    student_list = []
    for row in students:
        full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], row['extension'])
        student_list.append({
            "id": row['id'],
            "uid": format_uid(row['uid']) if row['uid'] else "—",
            "first_name": row['first_name'] or "—",
            "middle_name": row['middle_name'] or "—",
            "last_name": row['last_name'] or "—",
            "extension": row['extension'] or "—",
            "full_name": full_name if full_name else "—",
            "contact_number": row['contact_number'] or "—",
            "email": row['email'] or "—",
            "schedule": row['schedule'] or "—",
            "section_id": row['section_id'],
            "section_name": row['section_name'] or "—",
            "created_at": row['created_at'].strftime("%b %d, %Y  %I:%M %p") if row['created_at'] else "—"
        })
    return render_template('registered_students.html', students=student_list)

@admin_bp.route('/registered_students/api')
@login_required
def registered_students_api():
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({"success": False, "total": 0})
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM students")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"success": True, "total": count})
    except Exception as e:
        print(f"Error fetching student count: {e}")
        return jsonify({"success": False, "total": 0})

@admin_bp.route('/registered_students/data')
@login_required
def registered_students_data():
    students = get_all_students()
    student_list = []
    for row in students:
        full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], row['extension'])
        student_list.append({
            "id": row['id'],
            "uid": format_uid(row['uid']) if row['uid'] else "—",
            "full_name": full_name if full_name else "—",
            "contact_number": row['contact_number'] or "—",
            "email": row['email'] or "—",
            "schedule": row['schedule'] or "—",
            "section_id": row['section_id'],
            "section_name": row['section_name'] or "—",
            "created_at": row['created_at'].strftime("%Y-%m-%d") if row['created_at'] else "—"
        })
    return jsonify({"success": True, "students": student_list})

@admin_bp.route('/schedules')
@login_required
def schedules():
    conn = get_connection()
    if conn is None:
        return render_template('schedules.html', schedules=[])
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT s.id, s.uid, s.first_name, s.middle_name, s.last_name, s.extension,
                   s.contact_number, s.email, s.schedule, s.section_id, s.created_at, sec.section_name
            FROM students s LEFT JOIN sections sec ON s.section_id = sec.id
            WHERE s.schedule IS NOT NULL AND TRIM(s.schedule) != ''
            ORDER BY s.last_name ASC, s.first_name ASC, s.middle_name ASC;
        """)
        rows = cur.fetchall()
        cur.close()
        schedule_list = []
        for row in rows:
            full_name = build_full_name(row['first_name'], row['middle_name'], row['last_name'], row['extension'])
            schedule_list.append({
                "id": row['id'],
                "uid": format_uid(row['uid']) if row['uid'] else "—",
                "full_name": full_name if full_name else "—",
                "contact_number": row['contact_number'] or "—",
                "email": row['email'] or "—",
                "schedule": row['schedule'] or "—",
                "section_name": row['section_name'] or "—",
                "created_at": row['created_at'].strftime("%b %d, %Y  %I:%M %p") if row['created_at'] else "—"
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
    return render_template('history.html')

@admin_bp.route('/history/api')
@login_required
def history_api():
    conn = get_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database not connected", "history": []})
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.id, r.uid,
                COALESCE(NULLIF(TRIM(CONCAT(COALESCE(s.first_name, ''), ' ',
                    COALESCE(s.middle_name, ''), ' ', COALESCE(s.last_name, ''), ' ',
                    COALESCE(s.extension, ''))), ''), 'Unregistered Card') AS full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            ORDER BY r.created_at DESC LIMIT 500;
        """)
        rows = cur.fetchall()
        cur.close()
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
            SELECT r.id, r.uid,
                COALESCE(NULLIF(TRIM(CONCAT(COALESCE(s.first_name, ''), ' ',
                    COALESCE(s.middle_name, ''), ' ', COALESCE(s.last_name, ''), ' ',
                    COALESCE(s.extension, ''))), ''), 'Unregistered Card') AS full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            WHERE DATE(r.created_at) = %s ORDER BY r.created_at DESC;
        """, (selected_date,))
        rows = cur.fetchall()
        cur.close()
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
# TEACHER AUTHENTICATION
# =========================
def get_teacher_by_email(email):
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT t.id, t.last_name, t.first_name, t.middle_name, t.extension,
                   t.contact_number, t.email AS teacher_email, t.created_at,
                   ta.id AS account_id, ta.teacher_id AS linked_teacher_id,
                   ta.email AS account_email, ta.password, ta.reset_token
            FROM teacher_accounts ta
            INNER JOIN teachers t ON t.id = ta.teacher_id
            WHERE LOWER(TRIM(ta.email)) = LOWER(TRIM(%s)) LIMIT 1;
        """, (email,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        full_name = build_full_name(row["first_name"], row["middle_name"], row["last_name"], row["extension"])
        return {
            "id": row["id"], "teacher_id": row["linked_teacher_id"], "account_id": row["account_id"],
            "first_name": row["first_name"], "middle_name": row["middle_name"], "last_name": row["last_name"],
            "extension": row["extension"], "full_name": full_name, "contact_number": row["contact_number"],
            "teacher_email": row["teacher_email"], "email": row["account_email"],
            "password_hash": row["password"], "created_at": row["created_at"], "reset_token": row["reset_token"]
        }
    except Exception as e:
        print(f"get_teacher_by_email error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_teacher_by_id(teacher_db_id):
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT id, last_name, first_name, middle_name, extension, contact_number, email, created_at
            FROM teachers WHERE id = %s LIMIT 1;
        """, (teacher_db_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        full_name = build_full_name(row["first_name"], row["middle_name"], row["last_name"], row["extension"])
        return {
            "id": row["id"], "first_name": row["first_name"], "middle_name": row["middle_name"],
            "last_name": row["last_name"], "extension": row["extension"], "full_name": full_name,
            "contact_number": row["contact_number"], "email": row["email"], "created_at": row["created_at"]
        }
    except Exception as e:
        print(f"get_teacher_by_id error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_teacher_in_db(teacher_db_id, fields: dict):
    conn = get_connection()
    if conn is None:
        raise Exception("Database not connected")
    allowed = {"first_name", "middle_name", "last_name", "extension", "contact_number", "email"}
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
    except Exception as e:
        print(f"update_teacher_in_db error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def update_teacher_account_password_by_email(email, new_password):
    conn = get_connection()
    if conn is None:
        raise Exception("Database not connected")
    try:
        hashed_password = generate_password_hash(new_password)
        cur = conn.cursor()
        cur.execute("""
            UPDATE teacher_accounts SET password = %s
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s));
        """, (hashed_password, email))
        conn.commit()
        updated = cur.rowcount > 0
        cur.close()
        return updated
    except Exception as e:
        print(f"update_teacher_account_password_by_email error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def get_teacher_by_email_simple(email):
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return None
        cur = conn.cursor()
        cur.execute("SELECT email FROM teacher_accounts WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s)) LIMIT 1;", (email,))
        row = cur.fetchone()
        cur.close()
        if row:
            return {"email": row[0]}
        return None
    except Exception as e:
        print(f"get_teacher_by_email_simple error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def save_reset_token(email, token):
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return False
        cur = conn.cursor()
        cur.execute("UPDATE teacher_accounts SET reset_token = %s WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))", (token, email))
        conn.commit()
        updated = cur.rowcount > 0
        cur.close()
        return updated
    except Exception as e:
        print(f"Save reset token error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def verify_reset_token(token):
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return None
        cur = conn.cursor()
        cur.execute("SELECT email FROM teacher_accounts WHERE reset_token = %s", (token,))
        row = cur.fetchone()
        cur.close()
        if row:
            return row[0]
        return None
    except Exception as e:
        print(f"Verify reset token error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def clear_reset_token(email):
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return False
        cur = conn.cursor()
        cur.execute("UPDATE teacher_accounts SET reset_token = NULL WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))", (email,))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"Clear reset token error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def send_reset_email(to_email, reset_token):
    try:
        smtp_server = os.getenv("MAIL_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("MAIL_PORT", 587))
        smtp_username = os.getenv("MAIL_USERNAME")
        smtp_password = os.getenv("MAIL_PASSWORD")
        from_email = os.getenv("MAIL_FROM", smtp_username)
        
        if not smtp_username or not smtp_password:
            print("❌ Email credentials not configured")
            return False
        
        base_url = os.getenv("BASE_URL", "http://127.0.0.1:5000")
        reset_link = f"{base_url}/admin/reset_password/{reset_token}"
        
        subject = "Password Reset Request - Tap & Know System"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Password Reset</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #1e3a8a, #1e1b4b); color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .button {{ display: inline-block; padding: 12px 24px; background: #f59e0b; color: white; text-decoration: none; border-radius: 5px; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Tap & Know System</h2>
                </div>
                <div class="content">
                    <h3>Password Reset Request</h3>
                    <p>Hello,</p>
                    <p>We received a request to reset your password for your Tap & Know teacher account.</p>
                    <p style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </p>
                    <p>Or copy and paste this link into your browser:</p>
                    <p style="background: #eee; padding: 10px; word-break: break-all;">{reset_link}</p>
                    <p><strong>Important:</strong> This link will expire after 24 hours.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                    <hr>
                    <p style="font-size: 14px;">Best regards,<br>Tap & Know Administration</p>
                </div>
                <div class="footer">
                    <p>This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        msg.attach(MIMEText(html_content, 'html'))
        
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Password reset email sent to {to_email}")
        print(f"🔗 Reset link: {reset_link}")
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False

# =========================
# TEACHER LOGIN & PROFILE
# =========================
@admin_bp.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '').strip()
        if not identifier or not password:
            flash('Please enter email and password.', 'error')
            return render_template('login.html')
        teacher = get_teacher_by_email(identifier)
        if teacher:
            password_valid = False
            if teacher['password_hash']:
                password_valid = check_password_hash(teacher['password_hash'], password)
                if not password_valid and teacher['password_hash'] == password:
                    password_valid = True
            if password_valid:
                session.permanent = True
                session['teacher_logged_in'] = True
                session['teacher_id'] = teacher['id']
                session['teacher_account_id'] = teacher['account_id']
                session['teacher_email'] = teacher['email']
                session['teacher_name'] = teacher['full_name']
                flash('Login successful!', 'success')
                return redirect(url_for('admin_bp.teacher_profile'))
            else:
                flash('Invalid email or password.', 'error')
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
    try:
        data = request.get_json()
        teacher_id = session.get('teacher_id')
        if not teacher_id:
            return jsonify({"success": False, "message": "Not logged in"}), 401
        fields = {}
        for field in ['first_name', 'middle_name', 'last_name', 'extension', 'contact_number']:
            if field in data:
                fields[field] = data[field]
        if not fields:
            return jsonify({"success": False, "message": "No fields to update"}), 400
        update_teacher_in_db(teacher_id, fields)
        if 'first_name' in data or 'middle_name' in data or 'last_name' in data or 'extension' in data:
            updated_teacher = get_teacher_by_id(teacher_id)
            if updated_teacher:
                session['teacher_name'] = updated_teacher['full_name']
        return jsonify({"success": True, "message": "Profile updated successfully"})
    except Exception as e:
        print(f"update_teacher_profile error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# =========================
# FORGOT PASSWORD ROUTES
# =========================
@admin_bp.route('/forgot_password_ajax', methods=['POST'])
def forgot_password_ajax():
    try:
        data = request.get_json()
        email = data.get('email', '').strip()
        if not email:
            return jsonify({'success': False, 'message': 'Please enter your email address.'}), 400
        teacher = get_teacher_by_email_simple(email)
        if not teacher:
            return jsonify({'success': True, 'message': 'If an account exists with this email, you will receive password reset instructions.'})
        reset_token = str(uuid.uuid4())
        if save_reset_token(email, reset_token):
            if send_reset_email(email, reset_token):
                return jsonify({'success': True, 'message': 'Password reset instructions have been sent to your email.'})
            else:
                return jsonify({'success': False, 'message': 'Failed to send reset email.'}), 500
        else:
            return jsonify({'success': False, 'message': 'Failed to process request.'}), 500
    except Exception as e:
        print(f"Forgot password error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    print(f"Reset password route called with token: {token}")
    email = verify_reset_token(token)
    
    print(f"Email from token: {email}")
    
    if not email:
        flash('Invalid or expired reset link. Please request a new one.', 'error')
        return redirect(url_for('admin_bp.teacher_login'))
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        if not password:
            flash('Please enter a password.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
        elif password != confirm_password:
            flash('Passwords do not match.', 'error')
        else:
            try:
                if update_teacher_account_password_by_email(email, password):
                    clear_reset_token(email)
                    flash('Password has been reset successfully! Please login with your new password.', 'success')
                    return redirect(url_for('admin_bp.teacher_login'))
                else:
                    flash('Failed to reset password. Please try again.', 'error')
            except Exception as e:
                print(f"Password reset error: {e}")
                flash('An error occurred. Please try again.', 'error')
        
        return render_template('reset_password.html', token=token)
    
    return render_template('reset_password.html', token=token)

@admin_bp.route('/teacher/logout')
def teacher_logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('admin_bp.teacher_login'))

@admin_bp.route('/teacher/upload_profile_pic', methods=['POST'])
@login_required
def upload_teacher_profile_pic():
    return jsonify({"success": False, "message": "Profile picture upload is disabled"}), 501

@admin_bp.route('/debug/db_test')
def debug_db_test():
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({"status": "error", "message": "Cannot connect to database"})
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM teacher_accounts")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "Database connected", "teacher_accounts_count": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@admin_bp.route('/test_email_send')
@login_required
def test_email_send():
    try:
        to_email = session.get('teacher_email')
        if not to_email:
            return jsonify({"success": False, "message": "No email in session"})
        test_token = str(uuid.uuid4())
        result = send_reset_email(to_email, test_token)
        if result:
            return jsonify({"success": True, "message": f"Test email sent to {to_email}. Check your inbox/spam folder."})
        else:
            return jsonify({"success": False, "message": "Failed to send email. Check console for details."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# =========================
# TEACHER MY SCHEDULES ROUTES (Logged-in teacher only)
# =========================
@admin_bp.route('/teacher_schedules')
@login_required
def teacher_schedules():
    """Display sections for the logged-in teacher"""
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        flash('Teacher not found', 'error')
        return redirect(url_for('admin_bp.teacher_login'))
    
    conn = get_db_connection()
    if conn is None:
        return render_template('my_schedules.html', sections=[])
    
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get all sections assigned to the logged-in teacher
        cur.execute("""
            SELECT 
                s.id as section_id,
                s.section_name,
                s.year_level,
                COUNT(DISTINCT st.id) as student_count
            FROM sections s
            LEFT JOIN students st ON st.section_id = s.id
            WHERE s.teacher_id = %s
            GROUP BY s.id, s.section_name, s.year_level
            ORDER BY s.section_name
        """, (teacher_id,))
        
        sections = cur.fetchall()
        
        sections_data = []
        for section in sections:
            # Get schedule count for this section from schedules table
            cur2 = conn.cursor()
            cur2.execute("""
                SELECT COUNT(*) as schedule_count
                FROM schedules
                WHERE section_id = %s
            """, (section['section_id'],))
            schedule_count = cur2.fetchone()[0]
            cur2.close()
            
            sections_data.append({
                'section_id': section['section_id'],
                'section_name': section['section_name'],
                'year_level': section['year_level'],
                'student_count': section['student_count'] or 0,
                'schedule_count': schedule_count
            })
        
        conn.close()
        return render_template('my_schedules.html', sections=sections_data)
        
    except Exception as e:
        print(f"Teacher schedules error: {e}")
        if conn:
            conn.close()
        return render_template('my_schedules.html', sections=[])

@admin_bp.route('/section_weekly_schedule_data/<int:section_id>')
@login_required
def section_weekly_schedule_data(section_id):
    """API endpoint to get weekly schedule data for a section from schedules table"""
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        return jsonify({"success": False, "error": "Not logged in"})
    
    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "error": "Database connection failed"})
    
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Verify the section belongs to the logged-in teacher
        cur.execute("""
            SELECT id, section_name, year_level
            FROM sections
            WHERE id = %s AND teacher_id = %s
        """, (section_id, teacher_id))
        
        section = cur.fetchone()
        if not section:
            conn.close()
            return jsonify({"success": False, "error": "Section not found or unauthorized"})
        
        # Get all schedules for this section with teacher and room info
        cur.execute("""
            SELECT 
                s.id,
                s.day,
                s.time,
                s.subject,
                t.first_name as teacher_first,
                t.last_name as teacher_last,
                r.room_name
            FROM schedules s
            LEFT JOIN teachers t ON s.teacher_id = t.id
            LEFT JOIN rooms r ON s.room_id = r.id
            WHERE s.section_id = %s
            ORDER BY 
                CASE s.day
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    WHEN 'Sunday' THEN 7
                END,
                s.time
        """, (section_id,))
        
        schedules = cur.fetchall()
        conn.close()
        
        # Format schedule data for calendar view with hourly slots
        schedule_data = []
        for s in schedules:
            teacher_name = ""
            if s['teacher_first'] and s['teacher_last']:
                teacher_name = f"{s['teacher_first']} {s['teacher_last']}"
            
            time_str = str(s['time'])
            # Get hour from time
            hour = int(time_str.split(':')[0])
            
            # Create hourly time slot (e.g., "7:00-8:00")
            next_hour = hour + 1
            time_slot = f"{hour}:00-{next_hour}:00"
            
            schedule_data.append({
                'id': s['id'],
                'day': s['day'],
                'time': time_str,
                'hour': hour,
                'time_slot': time_slot,
                'formatted_time': f"{hour}:00 - {next_hour}:00",
                'subject': s['subject'] or 'No Subject',
                'teacher': teacher_name,
                'room': s['room_name'] or 'TBD'
            })
        
        section_data = {
            'section_id': section['id'],
            'section_name': section['section_name'],
            'year_level': section['year_level'],
            'schedules': schedule_data
        }
        
        return jsonify({"success": True, "section": section_data})
        
    except Exception as e:
        print(f"Section weekly schedule data error: {e}")
        if conn:
            conn.close()
        return jsonify({"success": False, "error": str(e)})
# =========================
# ROOMS API
# =========================
@admin_bp.route('/api/rooms')
@login_required
def get_rooms():
    """Get list of all rooms"""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "rooms": []})
    
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT id, room_name FROM rooms ORDER BY room_name")
        rooms = cur.fetchall()
        conn.close()
        
        rooms_list = []
        for r in rooms:
            rooms_list.append({
                'id': r['id'],
                'name': r['room_name']
            })
        
        return jsonify({"success": True, "rooms": rooms_list})
        
    except Exception as e:
        print(f"Get rooms error: {e}")
        if conn:
            conn.close()
        return jsonify({"success": False, "rooms": []})

# =========================
# STUDENT UPDATE ROUTE
# =========================
@admin_bp.route('/update_student/<int:student_id>', methods=['PUT'])
@login_required
def update_student(student_id):
    """Update student information"""
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    
    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database connection failed"}), 500
    
    try:
        data = request.get_json()
        cur = conn.cursor()
        
        # Verify teacher has access to this student
        cur.execute("""
            SELECT s.id FROM students s
            JOIN sections sec ON s.section_id = sec.id
            WHERE s.id = %s AND sec.teacher_id = %s
        """, (student_id, teacher_id))
        
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Unauthorized to edit this student"}), 403
        
        # Update student
        cur.execute("""
            UPDATE students SET 
                first_name = %s,
                middle_name = %s,
                last_name = %s,
                extension = %s,
                uid = %s,
                contact_number = %s,
                email = %s,
                schedule = %s
            WHERE id = %s
        """, (
            data.get('first_name', ''),
            data.get('middle_name', ''),
            data.get('last_name', ''),
            data.get('extension', ''),
            data.get('uid', ''),
            data.get('contact_number', ''),
            data.get('email', ''),
            data.get('schedule', ''),
            student_id
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"success": True, "message": "Student updated successfully"})
        
    except Exception as e:
        print(f"Update student error: {e}")
        if conn:
            conn.close()
        return jsonify({"success": False, "message": str(e)}), 500

@admin_bp.route('/student_info/<int:student_id>')
@login_required
def student_info(student_id):
    """Display detailed information about a student"""
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        flash('Please login first', 'error')
        return redirect(url_for('admin_bp.teacher_login'))
    
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error', 'error')
        return redirect(url_for('admin_bp.teacher_schedules'))
    
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get student details and verify teacher has access to this student's section
        cur.execute("""
            SELECT 
                st.id,
                st.uid,
                st.first_name,
                st.middle_name,
                st.last_name,
                st.extension,
                st.contact_number,
                st.email,
                st.schedule,
                st.created_at,
                s.id as section_id,
                s.section_name,
                s.year_level,
                t.id as teacher_id,
                t.first_name as teacher_first,
                t.last_name as teacher_last
            FROM students st
            LEFT JOIN sections s ON st.section_id = s.id
            LEFT JOIN teachers t ON s.teacher_id = t.id
            WHERE st.id = %s
        """, (student_id,))
        
        student = cur.fetchone()
        
        if not student:
            flash('Student not found', 'error')
            return redirect(url_for('admin_bp.teacher_schedules'))
        
        # Check if teacher has access to this student's section
        if student['teacher_id'] != teacher_id:
            flash('You do not have permission to view this student', 'error')
            return redirect(url_for('admin_bp.teacher_schedules'))
        
        # Get attendance history for this student
        cur.execute("""
            SELECT 
                DATE(created_at) as scan_date,
                MIN(created_at) as first_scan,
                COUNT(*) as scan_count
            FROM rfid_cards
            WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) 
                = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(%s, '')), ' ', ''), '-', ''), ':', ''))
            GROUP BY DATE(created_at)
            ORDER BY scan_date DESC
            LIMIT 30
        """, (student['uid'],))
        
        attendance_history = cur.fetchall()
        conn.close()
        
        # Build full name
        full_name_parts = [
            student['first_name'] or '',
            student['middle_name'] or '',
            student['last_name'] or '',
            student['extension'] or ''
        ]
        full_name = ' '.join([p for p in full_name_parts if p]).strip()
        
        return render_template('student_info.html', 
                             student=student, 
                             full_name=full_name,
                             attendance_history=attendance_history)
        
    except Exception as e:
        print(f"Student info error: {e}")
        if conn:
            conn.close()
        flash('Error loading student information', 'error')
        return redirect(url_for('admin_bp.teacher_schedules'))