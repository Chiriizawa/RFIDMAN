from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
import psycopg2
import serial
import threading
import time
import csv
from io import TextIOWrapper
from datetime import datetime
import pandas as pd

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
        print("get_all_uids error:", e)
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
            "INSERT INTO rfid_cards (uid) VALUES (%s);",
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


# =========================
# STUDENT FUNCTIONS
# =========================

def save_student_to_db(full_name, birthday, contact_number, email, schedule_text):
    if conn is None:
        raise Exception("Database not connected")

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO students (full_name, birthday, contact_number, email, schedule)
        VALUES (%s, %s, %s, %s, %s)
    """, (full_name, birthday, contact_number, email, schedule_text))
    cur.close()


def get_all_students():
    if conn is None:
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, full_name, birthday, contact_number, email, schedule, created_at
            FROM students
            ORDER BY created_at DESC, id DESC
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
        full_name = request.form.get('full_name', '').strip()
        birthday = request.form.get('birthday', '').strip()
        contact_number = request.form.get('contact_number', '').strip()
        email = request.form.get('email', '').strip()
        schedule_text = request.form.get('schedule', '').strip()

        if not full_name or not birthday or not contact_number or not email or not schedule_text:
            flash("Please fill in all fields.", "error")
            return redirect(url_for('admin_bp.register'))

        try:
            save_student_to_db(full_name, birthday, contact_number, email, schedule_text)
            flash("Student registered successfully.", "success")
            return redirect(url_for('admin_bp.register'))
        except Exception as e:
            print("Manual register error:", e)
            flash(f"Failed to save student: {str(e)}", "error")
            return redirect(url_for('admin_bp.register'))

    students = get_all_students()
    return render_template('register.html', students=students)


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
            flash("Only CSV or Excel (.xlsx, .xls) files are allowed.", "error")
            return redirect(url_for('admin_bp.register'))

        # normalize column names
        df.columns = [str(col).strip().lower() for col in df.columns]

        required_columns = ['full_name', 'birthday', 'contact_number', 'email', 'schedule']
        for col in required_columns:
            if col not in df.columns:
                flash(f"Missing column: {col}", "error")
                return redirect(url_for('admin_bp.register'))

        inserted_count = 0

        for _, row in df.iterrows():
            full_name = '' if pd.isna(row['full_name']) else str(row['full_name']).strip()
            birthday = '' if pd.isna(row['birthday']) else str(row['birthday']).strip()
            contact_number = '' if pd.isna(row['contact_number']) else str(row['contact_number']).strip()
            email = '' if pd.isna(row['email']) else str(row['email']).strip()
            schedule_text = '' if pd.isna(row['schedule']) else str(row['schedule']).strip()

            if not full_name and not birthday and not contact_number and not email and not schedule_text:
                continue

            if not full_name or not birthday or not contact_number or not email or not schedule_text:
                continue

            save_student_to_db(full_name, birthday, contact_number, email, schedule_text)
            inserted_count += 1

        flash(f"File imported successfully. {inserted_count} student(s) added.", "success")

    except Exception as e:
        print("Import error:", e)
        flash(f"Import failed: {str(e)}", "error")

    return redirect(url_for('admin_bp.register'))