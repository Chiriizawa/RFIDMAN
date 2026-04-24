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
# DB CONNECTION - SUPABASE VERSION (FIXED)
# =========================

def get_db_connection():
    try:
        print(f"Attempting to connect to database...")
        print(f"Host: {os.getenv('DB_HOST')}")
        print(f"Database: {os.getenv('DB_NAME')}")
        print(f"User: {os.getenv('DB_USER')}")
        
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT"),
            sslmode='require',
            connect_timeout=30
        )
        conn.autocommit = True
        print("✓ Database connection successful")
        return conn
    except Exception as e:
        print(f"✗ Database connection error: {e}")
        return None

def get_connection():
    """Get database connection with test"""
    try:
        conn = get_db_connection()
        if conn:
            # Test the connection
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        return None
    except Exception as e:
        print(f"Connection error in get_connection: {e}")
        return None

# Test connection on startup
print("\n=== Testing Database Connection ===")
test_conn = get_connection()
if test_conn:
    print("✓ Supabase connected successfully")
    test_conn.close()
else:
    print("✗ Supabase connection failed! Please check your .env file")
print("===================================\n")

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
        cur.close()
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
            return jsonify({
                "status": "error",
                "message": "Database connection failed. Check your .env file and Supabase credentials"
            })

        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        result = cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "Supabase connected successfully",
            "server_time": str(result[0])
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
    Login is based on teacher_accounts.email
    Password is based on teacher_accounts.password
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
                t.last_name,
                t.first_name,
                t.middle_name,
                t.extension,
                t.birthday,
                t.contact_number,
                t.email AS teacher_email,
                t.created_at,
                t.subject,
                t.department,
                t.bio,
                t.profile_picture,
                ta.id AS account_id,
                ta.teacher_id AS linked_teacher_id,
                ta.email AS account_email,
                ta.password,
                ta.reset_token
            FROM teacher_accounts ta
            INNER JOIN teachers t ON t.id = ta.teacher_id
            WHERE LOWER(TRIM(ta.email)) = LOWER(TRIM(%s))
            LIMIT 1;
        """, (email,))
        row = cur.fetchone()
        cur.close()

        if not row:
            print(f"No teacher account found with email: {email}")
            return None

        full_name = build_full_name(
            row["first_name"],
            row["middle_name"],
            row["last_name"],
            row["extension"]
        )

        return {
            "id": row["id"],
            "teacher_id": row["linked_teacher_id"],
            "account_id": row["account_id"],
            "first_name": row["first_name"],
            "middle_name": row["middle_name"],
            "last_name": row["last_name"],
            "extension": row["extension"],
            "birthday": row["birthday"],
            "full_name": full_name,
            "contact_number": row["contact_number"],
            "teacher_email": row["teacher_email"],
            "email": row["account_email"],
            "password_hash": row["password"],
            "created_at": row["created_at"],
            "reset_token": row["reset_token"],
            "subject": row.get("subject", ""),
            "department": row.get("department", ""),
            "bio": row.get("bio", ""),
            "profile_picture": row.get("profile_picture", "")
        }

    except Exception as e:
        print(f"get_teacher_by_email error: {e}")
        import traceback
        traceback.print_exc()
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
            SELECT
                id,
                last_name,
                first_name,
                middle_name,
                extension,
                birthday,
                contact_number,
                email,
                created_at,
                subject,
                department,
                bio,
                profile_picture
            FROM teachers
            WHERE id = %s
            LIMIT 1;
        """, (teacher_db_id,))
        row = cur.fetchone()
        cur.close()

        if not row:
            return None

        full_name = build_full_name(
            row["first_name"],
            row["middle_name"],
            row["last_name"],
            row["extension"]
        )

        return {
            "id": row["id"],
            "first_name": row["first_name"],
            "middle_name": row["middle_name"],
            "last_name": row["last_name"],
            "extension": row["extension"],
            "birthday": row["birthday"],
            "full_name": full_name,
            "contact_number": row["contact_number"],
            "email": row["email"],
            "created_at": row["created_at"],
            "subject": row.get("subject", ""),
            "department": row.get("department", ""),
            "bio": row.get("bio", ""),
            "profile_picture": row.get("profile_picture", "")
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

    allowed = {
        "first_name", "middle_name", "last_name", "extension", 
        "contact_number", "email", "birthday", 
        "subject", "department", "bio", "profile_picture"
    }
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
            UPDATE teacher_accounts
            SET password = %s
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s));
        """, (hashed_password, email))
        conn.commit()
        updated = cur.rowcount > 0
        cur.close()
        print(f"Password updated for {email}: {updated}")
        return updated
    except Exception as e:
        print(f"update_teacher_account_password_by_email error: {e}")
        raise
    finally:
        if conn:
            conn.close()

# =========================
# FORGOT PASSWORD FUNCTIONS
# =========================

def send_reset_email(to_email, reset_token):
    """Send password reset email using Gmail SMTP"""
    try:
        smtp_server = os.getenv("MAIL_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("MAIL_PORT", 587))
        smtp_username = os.getenv("MAIL_USERNAME")
        smtp_password = os.getenv("MAIL_PASSWORD")
        from_email = os.getenv("MAIL_FROM", smtp_username)
        
        print(f"Sending reset email to: {to_email}")
        
        reset_link = f"http://127.0.0.1:5000/reset_password/{reset_token}"
        
        subject = "Password Reset Request - Tap & Know"
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Password Reset</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background: #f9f9f9;
                    border-radius: 10px;
                }}
                .header {{
                    background: linear-gradient(135deg, #1e3a8a 0%, #1e1b4b 100%);
                    color: white;
                    padding: 20px;
                    text-align: center;
                    border-radius: 10px 10px 0 0;
                }}
                .content {{
                    background: white;
                    padding: 30px;
                    border-radius: 0 0 10px 10px;
                }}
                .button {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: linear-gradient(135deg, #1e3a8a 0%, #1e1b4b 100%);
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 20px;
                    font-size: 12px;
                    color: #666;
                }}
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
                    <p>Click the button below to reset your password:</p>
                    <p style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </p>
                    <p>Or copy and paste this link into your browser:</p>
                    <p style="background: #f0f0f0; padding: 10px; border-radius: 5px; word-break: break-all;">
                        {reset_link}
                    </p>
                    <p><strong>Important:</strong> This link will expire after 24 hours.</p>
                    <p>If you didn't request this password reset, please ignore this email.</p>
                    <hr>
                    <p style="font-size: 14px; color: #666;">
                        Best regards,<br>
                        Tap & Know Administration
                    </p>
                </div>
                <div class="footer">
                    <p>This is an automated message, please do not reply to this email.</p>
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
        
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        print(f"✓ Reset email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        print(f"✗ Email sending error: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_teacher_by_email_simple(email):
    """Simplified function just to check if email exists"""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            print("No database connection")
            return None
        
        cur = conn.cursor()
        cur.execute("""
            SELECT email FROM teacher_accounts
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
            LIMIT 1;
        """, (email,))
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
    """Save reset token to database"""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            print("No database connection")
            return False
        
        cur = conn.cursor()
        cur.execute("""
            UPDATE teacher_accounts
            SET reset_token = %s
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
        """, (token, email))
        conn.commit()
        updated = cur.rowcount > 0
        cur.close()
        
        print(f"Token saved for {email}: {updated}")
        return updated
        
    except Exception as e:
        print(f"Save reset token error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def verify_reset_token(token):
    """Verify if reset token exists and get associated email"""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return None
        
        cur = conn.cursor()
        cur.execute("""
            SELECT email FROM teacher_accounts
            WHERE reset_token = %s
        """, (token,))
        row = cur.fetchone()
        cur.close()
        
        if row:
            print(f"Token verified for: {row[0]}")
            return row[0]
        return None
        
    except Exception as e:
        print(f"Verify reset token error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def clear_reset_token(email):
    """Clear reset token after successful password reset"""
    conn = None
    try:
        conn = get_connection()
        if conn is None:
            return False
        
        cur = conn.cursor()
        cur.execute("""
            UPDATE teacher_accounts
            SET reset_token = NULL
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
        """, (email,))
        conn.commit()
        cur.close()
        print(f"Token cleared for: {email}")
        return True
        
    except Exception as e:
        print(f"Clear reset token error: {e}")
        return False
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
        password = request.form.get('password', '').strip()

        print(f"Login attempt for: {identifier}")

        if not identifier or not password:
            flash('Please enter email and password.', 'error')
            return render_template('login.html')

        teacher = get_teacher_by_email(identifier)

        if teacher:
            print(f"Teacher account found: {teacher['email']}")

            if teacher['password_hash'] and check_password_hash(teacher['password_hash'], password):
                session.permanent = True
                session['teacher_logged_in'] = True
                session['teacher_id'] = teacher['id']
                session['teacher_account_id'] = teacher['account_id']
                session['teacher_email'] = teacher['email']
                session['teacher_name'] = teacher['full_name']

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
        if "extension" in data:
            fields["extension"] = data["extension"]
        if "contact_number" in data:
            fields["contact_number"] = data["contact_number"]
        if "email" in data:
            fields["email"] = data["email"]
        if "birthday" in data:
            fields["birthday"] = data["birthday"]
        if "subject" in data:
            fields["subject"] = data["subject"]
        if "department" in data:
            fields["department"] = data["department"]
        if "bio" in data:
            fields["bio"] = data["bio"]

        update_teacher_in_db(teacher_id, fields)
        return jsonify({"success": True, "message": "Profile updated successfully"})

    except Exception as e:
        print(f"update_teacher_profile error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# =========================
# FORGOT PASSWORD ROUTES
# =========================

@admin_bp.route('/forgot_password_ajax', methods=['POST'])
def forgot_password_ajax():
    """Handle forgot password AJAX request"""
    print("\n=== Forgot Password Request ===")
    
    try:
        data = request.get_json()
        email = data.get('email', '').strip()
        
        print(f"Email: {email}")
        
        if not email:
            return jsonify({
                'success': False,
                'message': 'Please enter your email address.'
            }), 400
        
        # Check if email exists
        teacher = get_teacher_by_email_simple(email)
        
        if not teacher:
            print(f"Email not found: {email}")
            return jsonify({
                'success': True,
                'message': 'If an account exists with this email, you will receive password reset instructions.'
            })
        
        print(f"Email found: {email}")
        
        # Generate token
        reset_token = str(uuid.uuid4())
        print(f"Token generated: {reset_token}")
        
        # Save token
        if save_reset_token(email, reset_token):
            # Send email
            if send_reset_email(email, reset_token):
                return jsonify({
                    'success': True,
                    'message': 'Password reset instructions have been sent to your email.'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to send reset email. Please check your email configuration.'
                }), 500
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to process request. Please try again.'
            }), 500
            
    except Exception as e:
        print(f"Forgot password error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'An error occurred: {str(e)}'
        }), 500

@admin_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Handle password reset page"""
    print(f"\n=== Reset Password Request ===")
    print(f"Token: {token[:20]}...")
    
    email = verify_reset_token(token)
    
    if not email:
        flash('Invalid or expired reset link. Please request a new one.', 'error')
        return redirect(url_for('admin_bp.teacher_login'))
    
    print(f"Email found: {email}")
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        if not password:
            flash('Please enter a password.', 'error')
            return render_template('reset_password.html', token=token)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('reset_password.html', token=token)
        
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)
        
        try:
            if update_teacher_account_password_by_email(email, password):
                clear_reset_token(email)
                flash('Password has been reset successfully! Please login with your new password.', 'success')
                print(f"Password reset successful for: {email}")
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
        filename = secure_filename(f"teacher_{teacher_id}_{int(time.time())}_{file.filename}")
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)

        image_url = f"/static/uploads/teachers/{filename}"
        
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE teachers SET profile_picture = %s WHERE id = %s", (image_url, teacher_id))
            conn.commit()
            cur.close()
            conn.close()
            
            return jsonify({
                "success": True,
                "image_url": image_url,
                "message": "Profile picture uploaded successfully"
            })
        except Exception as e:
            print(f"Error saving profile picture: {e}")
            return jsonify({"success": False, "message": "Database error"})
    
    return jsonify({"success": False, "message": "Upload failed"})

# =========================
# DEBUG ROUTE
# =========================

@admin_bp.route('/debug/db_test')
def debug_db_test():
    """Debug route to test database connection"""
    try:
        conn = get_connection()
        if conn is None:
            return jsonify({
                "status": "error",
                "message": "Cannot connect to database",
                "config": {
                    "host": os.getenv("DB_HOST"),
                    "database": os.getenv("DB_NAME"),
                    "user": os.getenv("DB_USER"),
                    "port": os.getenv("DB_PORT")
                }
            })
        
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM teacher_accounts")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Database connected successfully",
            "teacher_accounts_count": count
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })