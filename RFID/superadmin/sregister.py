from flask import Blueprint, render_template, flash, request, redirect, url_for, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
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

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        pass

    try:
        if raw.replace('.', '', 1).isdigit():
            serial = float(raw)
            excel_start = datetime(1899, 12, 30)
            converted = excel_start + timedelta(days=serial)
            return converted.date()
    except Exception:
        pass

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
    sections = []
    teachers = []

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Students with section + teacher
        cur.execute("""
            SELECT
                s.id,
                s.uid,
                s.last_name,
                s.first_name,
                s.middle_name,
                s.extension,
                s.birthday,
                s.contact_number,
                s.email,
                s.schedule,
                s.created_at,
                s.section_id,
                sec.section_name,
                sec.year_level,
                CASE
                    WHEN t.id IS NOT NULL THEN
                        TRIM(
                            COALESCE(t.last_name, '') || ', ' ||
                            COALESCE(t.first_name, '') ||
                            CASE
                                WHEN t.middle_name IS NOT NULL AND t.middle_name <> '' THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS teacher_name
            FROM students s
            LEFT JOIN sections sec ON s.section_id = sec.id
            LEFT JOIN teachers t ON sec.teacher_id = t.id
            ORDER BY s.id ASC
        """)
        students = cur.fetchall()

        # Available UIDs (not yet linked to any student)
        cur.execute("""
            SELECT rc.uid
            FROM rfid_cards rc
            LEFT JOIN students s ON s.uid = rc.uid
            WHERE s.uid IS NULL
            ORDER BY rc.id ASC
        """)
        available_uids = [row["uid"] for row in cur.fetchall()]

        # All sections
        cur.execute("""
            SELECT
                sec.id,
                sec.section_name,
                sec.year_level,
                sec.teacher_id,
                CASE
                    WHEN t.id IS NOT NULL THEN
                        TRIM(
                            COALESCE(t.last_name, '') || ', ' ||
                            COALESCE(t.first_name, '') ||
                            CASE
                                WHEN t.middle_name IS NOT NULL AND t.middle_name <> '' THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS teacher_name
            FROM sections sec
            LEFT JOIN teachers t ON sec.teacher_id = t.id
            ORDER BY sec.section_name ASC
        """)
        sections = cur.fetchall()

        # Teachers (no extension column in teachers table)
        cur.execute("""
            SELECT
                id,
                teacher_id,
                first_name,
                middle_name,
                last_name,
                TRIM(
                    last_name || ', ' || first_name ||
                    CASE
                        WHEN middle_name IS NOT NULL AND middle_name <> '' THEN ' ' || middle_name
                        ELSE ''
                    END
                ) AS full_name
            FROM teachers
            ORDER BY last_name ASC, first_name ASC
        """)
        teachers = cur.fetchall()

        cur.close()
        conn.close()

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")

    return render_template(
        "superadmin/student.html",
        students=students,
        available_uids=available_uids,
        sections=sections,
        teachers=teachers
    )


@sregister.route('/add-student', methods=['POST'])
def add_student():
    uid                   = request.form.get('uid', '').strip()
    existing_section_id   = request.form.get('section_id', '').strip()
    new_section_name      = request.form.get('new_section_name', '').strip()
    new_year_level        = request.form.get('new_year_level', '').strip()
    new_teacher_id_raw    = request.form.get('new_teacher_id', '').strip()

    last_name             = request.form.get('last_name', '').strip()
    first_name            = request.form.get('first_name', '').strip()
    middle_name           = request.form.get('middle_name', '').strip()
    extension             = request.form.get('extension', '').strip()
    birthday_raw          = request.form.get('birthday', '').strip()
    contact_number        = request.form.get('contact_number', '').strip()
    email                 = request.form.get('email', '').strip()
    schedule              = request.form.get('schedule', '').strip()

    if not last_name or not first_name or not birthday_raw or not contact_number or not email or not schedule:
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('sregister.student'))

    try:
        birthday       = normalize_birthday(birthday_raw)
        section_id     = int(existing_section_id) if existing_section_id else None
        new_teacher_id = int(new_teacher_id_raw) if new_teacher_id_raw else None

        conn = get_db_connection()
        cur  = conn.cursor()

        # Duplicate email check
        cur.execute("SELECT id FROM students WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate student check
        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s AND first_name = %s AND birthday = %s
        """, (last_name, first_name, birthday))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This student is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # --- SECTION LOGIC ---
        if section_id is not None:
            # Validate existing section
            cur.execute("SELECT id FROM sections WHERE id = %s", (section_id,))
            if not cur.fetchone():
                cur.close(); conn.close()
                flash("Selected section does not exist.", "error")
                return redirect(url_for('sregister.student'))

        elif new_section_name:
            # Validate teacher if provided
            if new_teacher_id is not None:
                cur.execute("SELECT id FROM teachers WHERE id = %s", (new_teacher_id,))
                if not cur.fetchone():
                    cur.close(); conn.close()
                    flash("Selected teacher does not exist.", "error")
                    return redirect(url_for('sregister.student'))

            # Check if section name already exists
            cur.execute("""
                SELECT id FROM sections WHERE LOWER(section_name) = LOWER(%s) LIMIT 1
            """, (new_section_name,))
            existing = cur.fetchone()

            if existing:
                section_id = existing[0]
            else:
                cur.execute("""
                    INSERT INTO sections (section_name, year_level, teacher_id)
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (
                    new_section_name,
                    new_year_level if new_year_level else None,
                    new_teacher_id
                ))
                section_id = cur.fetchone()[0]

        # --- UID LOGIC ---
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
                cur.close(); conn.close()
                flash("No available UID found.", "error")
                return redirect(url_for('sregister.student'))
            uid = uid_row[0]
        else:
            cur.execute("SELECT uid FROM rfid_cards WHERE uid = %s", (uid,))
            if not cur.fetchone():
                cur.close(); conn.close()
                flash("Selected UID does not exist in RFID cards.", "error")
                return redirect(url_for('sregister.student'))

            cur.execute("SELECT id FROM students WHERE uid = %s", (uid,))
            if cur.fetchone():
                cur.close(); conn.close()
                flash("This UID is already linked to another student.", "error")
                return redirect(url_for('sregister.student'))

        # --- INSERT STUDENT ---
        cur.execute("""
            INSERT INTO students (
                uid, last_name, first_name, middle_name, extension,
                birthday, contact_number, email, schedule, section_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            uid,
            last_name,
            first_name,
            middle_name   if middle_name   else None,
            extension     if extension     else None,
            birthday,
            contact_number,
            email,
            schedule,
            section_id
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
        cur  = conn.cursor()

        inserted_count = 0
        skipped_count  = 0

        for student in students:
            last_name      = str(student.get('last_name',      '')).strip()
            first_name     = str(student.get('first_name',     '')).strip()
            middle_name    = str(student.get('middle_name',    '')).strip()
            extension      = str(student.get('extension',      '')).strip()
            birthday_raw   = str(student.get('birthday',       '')).strip()
            contact_number = str(student.get('contact_number', '')).strip()
            email          = str(student.get('email',          '')).strip()
            schedule       = str(student.get('schedule',       '')).strip()
            section_name   = str(student.get('section_name',   '')).strip()
            year_level     = str(student.get('year_level',     '')).strip()

            if not last_name or not first_name or not birthday_raw or not contact_number or not email or not schedule:
                skipped_count += 1
                continue

            birthday   = normalize_birthday(birthday_raw)
            section_id = None

            cur.execute("SELECT id FROM students WHERE email = %s", (email,))
            if cur.fetchone():
                skipped_count += 1
                continue

            cur.execute("""
                SELECT id FROM students
                WHERE last_name = %s AND first_name = %s AND birthday = %s
            """, (last_name, first_name, birthday))
            if cur.fetchone():
                skipped_count += 1
                continue

            if section_name:
                cur.execute("""
                    SELECT id FROM sections WHERE LOWER(section_name) = LOWER(%s) LIMIT 1
                """, (section_name,))
                section_row = cur.fetchone()

                if section_row:
                    section_id = section_row[0]
                else:
                    cur.execute("""
                        INSERT INTO sections (section_name, year_level, teacher_id)
                        VALUES (%s, %s, %s)
                        RETURNING id
                    """, (section_name, year_level if year_level else None, None))
                    section_id = cur.fetchone()[0]

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
                    birthday, contact_number, email, schedule, section_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                uid,
                last_name,
                first_name,
                middle_name   if middle_name   else None,
                extension     if extension     else None,
                birthday,
                contact_number,
                email,
                schedule,
                section_id
            ))

            inserted_count += 1

        conn.commit()
        cur.close()
        conn.close()

        if inserted_count == 0:
            return jsonify({
                "message": "No valid student rows were imported. Check for duplicate emails, duplicate students, missing fields, birthday format, or no available unlinked UID."
            }), 400

        return jsonify({
            "message": f"Import successful. {inserted_count} student(s) added, {skipped_count} row(s) skipped."
        }), 200

    except Exception as e:
        return jsonify({"message": f"Import failed: {str(e)}"}), 500