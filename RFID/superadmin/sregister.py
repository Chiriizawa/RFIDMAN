from flask import Blueprint, render_template, flash, request, redirect, url_for, jsonify
import psycopg2
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

load_dotenv()

sregister = Blueprint("sregister", __name__, template_folder="template")


def normalize_birthday(value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    # already yyyy-mm-dd
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        pass

    # excel serial date
    try:
        if raw.replace('.', '', 1).isdigit():
            serial = float(raw)
            excel_start = datetime(1899, 12, 30)
            converted = excel_start + timedelta(days=serial)
            return converted.date()
    except Exception:
        pass

    # try common formats
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Invalid birthday format: {raw}")


def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432),
        sslmode="require"
    )
    return conn


@sregister.route('/test-db')
def test_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user;")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return f"Connected to DB: {result[0]} as {result[1]}"
    except Exception as e:
        return f"Database connection error: {str(e)}"


@sregister.route('/Student', methods=['GET'])
def student():
    students = []
    available_uids = []

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, uid, last_name, first_name, middle_name, extension,
                   birthday, contact_number, email, schedule, created_at
            FROM students
            ORDER BY id ASC
        """)
        rows = cur.fetchall()

        students = [
            {
                "id": r[0],
                "uid": r[1],
                "last_name": r[2],
                "first_name": r[3],
                "middle_name": r[4],
                "extension": r[5],
                "birthday": r[6],
                "contact_number": r[7],
                "email": r[8],
                "schedule": r[9],
                "created_at": r[10],
            }
            for r in rows
        ]

        cur.execute("""
            SELECT rc.uid
            FROM rfid_cards rc
            LEFT JOIN students s ON s.uid = rc.uid
            WHERE s.uid IS NULL
            ORDER BY rc.id ASC
        """)
        available_uids = [row[0] for row in cur.fetchall()]

        cur.close()
        conn.close()

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")

    return render_template(
        "superadmin/student.html",
        students=students,
        available_uids=available_uids
    )


@sregister.route('/add-student', methods=['POST'])
def add_student():
    uid = request.form.get('uid', '').strip()
    last_name = request.form.get('last_name', '').strip()
    first_name = request.form.get('first_name', '').strip()
    middle_name = request.form.get('middle_name', '').strip()
    extension = request.form.get('extension', '').strip()
    birthday_raw = request.form.get('birthday', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    email = request.form.get('email', '').strip()
    schedule = request.form.get('schedule', '').strip()

    if not last_name or not first_name or not birthday_raw or not contact_number or not email or not schedule:
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('sregister.student'))

    try:
        birthday = normalize_birthday(birthday_raw)

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id FROM students WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s
              AND first_name = %s
              AND birthday = %s
        """, (last_name, first_name, birthday))
        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This student is already registered.", "error")
            return redirect(url_for('sregister.student'))

        if not uid:
            cur.execute("""
                SELECT rc.uid
                FROM rfid_cards rc
                LEFT JOIN students s ON s.uid = rc.uid
                WHERE s.uid IS NULL
                ORDER BY rc.id ASC
                LIMIT 1
            """)
            uid_row = cur.fetchone()
            if not uid_row:
                cur.close()
                conn.close()
                flash("No available UID found.", "error")
                return redirect(url_for('sregister.student'))
            uid = uid_row[0]
        else:
            cur.execute("SELECT uid FROM rfid_cards WHERE uid = %s", (uid,))
            if not cur.fetchone():
                cur.close()
                conn.close()
                flash("Selected UID does not exist in RFID cards.", "error")
                return redirect(url_for('sregister.student'))

            cur.execute("SELECT id FROM students WHERE uid = %s", (uid,))
            if cur.fetchone():
                cur.close()
                conn.close()
                flash("This UID is already linked to another student.", "error")
                return redirect(url_for('sregister.student'))

        cur.execute("""
            INSERT INTO students (
                uid, last_name, first_name, middle_name, extension,
                birthday, contact_number, email, schedule
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            uid,
            last_name,
            first_name,
            middle_name if middle_name else None,
            extension if extension else None,
            birthday,
            contact_number,
            email,
            schedule
        ))

        conn.commit()
        cur.close()
        conn.close()

        flash("Student added successfully.", "success")
        return redirect(url_for('sregister.student'))

    except Exception as e:
        flash(f"Error adding student: {str(e)}", "error")
        return redirect(url_for('sregister.student'))


@sregister.route('/import-excel', methods=['POST'])
def import_excel():
    try:
        data = request.get_json(silent=True)

        if not data or 'students' not in data:
            return jsonify({"message": "No student data received."}), 400

        students = data.get('students', [])

        if not students:
            return jsonify({"message": "No valid student rows found."}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        inserted_count = 0
        skipped_count = 0

        for student in students:
            last_name = str(student.get('last_name', '')).strip()
            first_name = str(student.get('first_name', '')).strip()
            middle_name = str(student.get('middle_name', '')).strip()
            extension = str(student.get('extension', '')).strip()
            birthday_raw = str(student.get('birthday', '')).strip()
            contact_number = str(student.get('contact_number', '')).strip()
            email = str(student.get('email', '')).strip()
            schedule = str(student.get('schedule', '')).strip()

            if not last_name or not first_name or not birthday_raw or not contact_number or not email or not schedule:
                skipped_count += 1
                continue

            birthday = normalize_birthday(birthday_raw)

            cur.execute("SELECT id FROM students WHERE email = %s", (email,))
            if cur.fetchone():
                skipped_count += 1
                continue

            cur.execute("""
                SELECT id FROM students
                WHERE last_name = %s
                  AND first_name = %s
                  AND birthday = %s
            """, (last_name, first_name, birthday))
            if cur.fetchone():
                skipped_count += 1
                continue

            cur.execute("""
                SELECT rc.uid
                FROM rfid_cards rc
                LEFT JOIN students s ON s.uid = rc.uid
                WHERE s.uid IS NULL
                ORDER BY rc.id ASC
                LIMIT 1
            """)
            uid_row = cur.fetchone()

            if not uid_row:
                skipped_count += 1
                continue

            uid = uid_row[0]

            cur.execute("""
                INSERT INTO students (
                    uid, last_name, first_name, middle_name, extension,
                    birthday, contact_number, email, schedule
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                uid,
                last_name,
                first_name,
                middle_name if middle_name else None,
                extension if extension else None,
                birthday,
                contact_number,
                email,
                schedule
            ))

            inserted_count += 1

        conn.commit()
        cur.close()
        conn.close()

        if inserted_count == 0:
            return jsonify({
                "message": "No valid student rows were imported. Check duplicate emails, duplicate students, missing fields, birthday format, or no available unlinked UID."
            }), 400

        return jsonify({
            "message": f"Import successful. {inserted_count} student(s) added, {skipped_count} row(s) skipped."
        }), 200

    except Exception as e:
        return jsonify({"message": f"Import failed: {str(e)}"}), 500