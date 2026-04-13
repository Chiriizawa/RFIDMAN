from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
import psycopg2
import serial
import threading
import time
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()  # Load variables from .env file

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


def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().upper().split())


def normalize_email(email):
    if not email:
        return ""
    return str(email).strip().lower()


def normalize_contact(contact):
    if not contact:
        return ""
    return "".join(ch for ch in str(contact) if ch.isdigit())


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


@admin_bp.route('/get_all_uids')
def get_all_uids():
    ensure_connection()
    if conn is None:
        return jsonify([])

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                r.id,
                r.uid,
                s.full_name,
                r.created_at
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                 = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            ORDER BY r.id DESC;
        """)
        rows = cur.fetchall()
        cur.close()

        return jsonify([
            {
                "id": row[0],
                "uid": format_uid(row[1]),
                "full_name": row[2] if row[2] else "No linked student",
                "created_at": row[3].strftime("%Y-%m-%d %H:%M:%S") if row[3] else ""
            }
            for row in rows
        ])
    except Exception as e:
        print("get_all_uids error:", e)
        return jsonify([])


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
# RFID SAVE
# =========================

def save_uid_to_db(uid):
    ensure_connection()
    if conn is None:
        print("No database connection.")
        return

    uid = normalize_uid(uid)

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, uid
            FROM rfid_cards
            WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(uid), ' ', ''), '-', ''), ':', '')) = %s
            LIMIT 1;
        """, (uid,))
        existing = cur.fetchone()

        if existing:
            print(f"UID already exists: {existing[1]}")
            cur.close()
            return

        cur.execute("""
            INSERT INTO rfid_cards (uid)
            VALUES (%s);
        """, (uid,))
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

        print(f"LOOKUP UID: {uid} -> RESULT: {row}")

        if row:
            return row[0]
        return ""
    except Exception as e:
        print("get_student_name_by_uid error:", e)
        return ""


def listen():
    global latest_scan

    while True:
        try:
            if ser and ser.is_open and ser.in_waiting > 0:
                raw = ser.readline().decode('utf-8', errors='ignore').strip()
                print("RAW SERIAL:", repr(raw))

                if not raw:
                    time.sleep(0.2)
                    continue

                if not raw.upper().startswith("UID:"):
                    print("Ignored non-UID line:", raw)
                    time.sleep(0.2)
                    continue

                uid = raw.split(":", 1)[1].strip()
                uid = normalize_uid(uid)

                if not is_valid_uid(uid):
                    print("Ignored invalid UID:", uid)
                    time.sleep(0.2)
                    continue

                print("Scanned UID:", uid)

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
# UID + STUDENT FUNCTIONS
# =========================

def get_unlinked_uids():
    ensure_connection()
    if conn is None:
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.uid
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                 = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            WHERE s.uid IS NULL
            ORDER BY r.id ASC;
        """)
        rows = cur.fetchall()
        cur.close()
        return [format_uid(row[0]) for row in rows]
    except Exception as e:
        print("get_unlinked_uids error:", e)
        return []


def find_existing_student(full_name, birthday, contact_number, email):
    ensure_connection()
    if conn is None:
        return None

    full_name_norm = normalize_text(full_name)
    email_norm = normalize_email(email)
    contact_norm = normalize_contact(contact_number)

    try:
        cur = conn.cursor()

        if email_norm:
            cur.execute("""
                SELECT id, uid, full_name, birthday, contact_number, email
                FROM students
                WHERE LOWER(TRIM(COALESCE(email, ''))) = %s
                LIMIT 1;
            """, (email_norm,))
            row = cur.fetchone()
            if row:
                cur.close()
                return {
                    "reason": "Duplicate email",
                    "data": row
                }

        if contact_norm:
            cur.execute("""
                SELECT id, uid, full_name, birthday, contact_number, email
                FROM students
                WHERE REGEXP_REPLACE(COALESCE(contact_number, ''), '[^0-9]', '', 'g') = %s
                LIMIT 1;
            """, (contact_norm,))
            row = cur.fetchone()
            if row:
                cur.close()
                return {
                    "reason": "Duplicate contact number",
                    "data": row
                }

        cur.execute("""
            SELECT id, uid, full_name, birthday, contact_number, email
            FROM students
            WHERE UPPER(TRIM(COALESCE(full_name, ''))) = %s
              AND birthday = %s
            LIMIT 1;
        """, (full_name_norm, birthday))
        row = cur.fetchone()
        cur.close()

        if row:
            return {
                "reason": "Duplicate student details (same full name and birthday)",
                "data": row
            }

        return None

    except Exception as e:
        print("find_existing_student error:", e)
        return None


def save_student_to_db(uid, full_name, birthday, contact_number, email, schedule_text):
    ensure_connection()
    if conn is None:
        raise Exception("Database not connected")

    uid = normalize_uid(uid)
    full_name = full_name.strip()
    email = email.strip().lower()
    contact_number = contact_number.strip()

    cur = conn.cursor()

    cur.execute("""
        SELECT uid
        FROM rfid_cards
        WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) = %s
        LIMIT 1;
    """, (uid,))
    existing_card = cur.fetchone()

    if not existing_card:
        cur.close()
        raise Exception(f"UID '{uid}' does not exist in rfid_cards. Tap the card first.")

    cur.execute("""
        SELECT id, full_name
        FROM students
        WHERE UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(uid, '')), ' ', ''), '-', ''), ':', '')) = %s
        LIMIT 1;
    """, (uid,))
    uid_owner = cur.fetchone()

    if uid_owner:
        cur.close()
        raise Exception(f"UID '{uid}' is already linked to student '{uid_owner[1]}'.")

    cur.close()

    existing_student = find_existing_student(full_name, birthday, contact_number, email)
    if existing_student:
        reason = existing_student["reason"]
        existing_uid = existing_student["data"][1] if existing_student["data"][1] else "None"
        raise Exception(f"{reason}. Existing UID: {existing_uid}")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO students (uid, full_name, birthday, contact_number, email, schedule)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (uid, full_name, birthday, contact_number, email, schedule_text))
    cur.close()


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


# =========================
# REGISTER PAGE
# =========================

@admin_bp.route('/Register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        uid = normalize_uid(request.form.get('uid', '').strip())
        full_name = request.form.get('full_name', '').strip()
        birthday = request.form.get('birthday', '').strip()
        contact_number = request.form.get('contact_number', '').strip()
        email = request.form.get('email', '').strip()
        schedule_text = request.form.get('schedule', '').strip()

        if not uid or not full_name or not birthday or not contact_number or not email or not schedule_text:
            flash("Please fill in all fields including UID.", "error")
            return redirect(url_for('admin_bp.register'))

        if not is_valid_uid(uid):
            flash("Invalid UID format.", "error")
            return redirect(url_for('admin_bp.register'))

        try:
            save_student_to_db(uid, full_name, birthday, contact_number, email, schedule_text)
            flash("Student registered successfully and linked to UID.", "success")
            return redirect(url_for('admin_bp.register'))
        except Exception as e:
            print("Manual register error:", e)
            flash(str(e), "error")
            return redirect(url_for('admin_bp.register'))

    students = get_all_students()
    available_uids = get_unlinked_uids()
    return render_template('register.html', students=students, available_uids=available_uids)


# =========================
# IMPORT CSV OR EXCEL
# =========================

@admin_bp.route('/import_csv', methods=['POST'])
def import_csv():
    file = request.files.get('csv_file')

    if not file or file.filename == '':
        flash("Please select a file.", "error")
        return redirect(url_for('admin_bp.register'))

    filename = file.filename.lower()

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            df = pd.read_excel(file)
        else:
            flash("Only CSV or Excel (.csv, .xlsx, .xls) files are allowed.", "error")
            return redirect(url_for('admin_bp.register'))

        df.columns = [str(col).strip().lower() for col in df.columns]

        required_columns = ['full_name', 'birthday', 'contact_number', 'email', 'schedule']
        for col in required_columns:
            if col not in df.columns:
                flash(f"Missing column: {col}", "error")
                return redirect(url_for('admin_bp.register'))

        available_uids = get_unlinked_uids()

        inserted_count = 0
        errors = []
        uid_index = 0

        for i, row in df.iterrows():
            row_number = i + 2

            full_name = '' if pd.isna(row.get('full_name')) else str(row.get('full_name')).strip()
            birthday = '' if pd.isna(row.get('birthday')) else str(row.get('birthday')).strip()
            contact_number = '' if pd.isna(row.get('contact_number')) else str(row.get('contact_number')).strip()
            email = '' if pd.isna(row.get('email')) else str(row.get('email')).strip()
            schedule_text = '' if pd.isna(row.get('schedule')) else str(row.get('schedule')).strip()

            if not full_name or not birthday or not contact_number or not email or not schedule_text:
                errors.append(f"Row {row_number}: Missing required fields")
                continue

            existing_student = find_existing_student(
                full_name,
                birthday,
                contact_number,
                email
            )

            if existing_student:
                reason = existing_student["reason"]
                existing_uid = existing_student["data"][1] if existing_student["data"][1] else "None"
                errors.append(
                    f"Row {row_number}: {reason} for '{full_name}'. Existing UID: {existing_uid}"
                )
                continue

            if uid_index >= len(available_uids):
                errors.append(f"Row {row_number}: No available UID card")
                continue

            uid_to_use = normalize_uid(available_uids[uid_index])
            uid_index += 1

            if not is_valid_uid(uid_to_use):
                errors.append(f"Row {row_number}: Invalid UID assigned ({uid_to_use})")
                continue

            try:
                save_student_to_db(
                    uid_to_use,
                    full_name,
                    birthday,
                    contact_number,
                    email,
                    schedule_text
                )
                inserted_count += 1
            except Exception as e:
                errors.append(f"Row {row_number}: {str(e)}")

        if inserted_count > 0:
            flash(f"{inserted_count} student(s) successfully added.", "success")

        if errors:
            preview_errors = errors[:10]
            message = "Import errors:\n" + "\n".join(preview_errors)
            if len(errors) > 10:
                message += f"\n...and {len(errors) - 10} more error(s)"
            flash(message, "error")

        if inserted_count == 0 and not errors:
            flash("No valid student rows found in the file.", "error")

    except Exception as e:
        print("Import error:", e)
        flash(f"Import failed: {str(e)}", "error")

    return redirect(url_for('admin_bp.register'))

# =========================
# REGISTERED STUDENTS PAGE (Mga na-scan at naka-register na students)
# =========================

@admin_bp.route('/registered_students')
def registered_students():
    students = get_all_students()   # Gamitin ang existing function mo

    # I-convert para madaling gamitin sa HTML
    student_list = []
    for row in students:
        student_list.append({
            "id": row[0],
            "uid": format_uid(row[1]) if row[1] else "—",
            "full_name": row[2] or "—",
            "birthday": str(row[3]) if row[3] else "—",
            "contact_number": row[4] or "—",
            "email": row[5] or "—",
            "schedule": row[6] or "—",
            "created_at": row[7].strftime("%b %d, %Y  %I:%M %p") if row[7] else "—"
        })

    return render_template('registered_students.html', students=student_list)
