from flask import Blueprint, render_template, flash, request, redirect, url_for, jsonify, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

load_dotenv()

sregister = Blueprint("sregister", __name__, template_folder="template")


# ─────────────────────────────────────────────
# AUTH DECORATOR
# ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("sadmin_logged_in"):
            flash("Please log in to access the admin portal.", "warning")
            return redirect(url_for("sadmin.login"))
        return f(*args, **kwargs)
    return decorated_function


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

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
    """
    🔥 THIS IS YOUR ONLY DB CONNECTION

    ❌ NO SUPABASE
    ❌ NO LOCALHOST
    ❌ NO MULTIPLE CONFIGS

    ✅ ONLY RAILWAY DATABASE_URL
    """

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise Exception("❌ DATABASE_URL not found in .env")

    return psycopg2.connect(
        database_url.strip(),
        sslmode="require"
    )


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@sregister.route('/test-db')
@login_required
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
@login_required
def student():
    students = []
    available_uids = []
    sections = []
    teachers = []

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

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
                                WHEN t.middle_name IS NOT NULL
                                     AND t.middle_name <> ''
                                THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS teacher_name

            FROM students s

            LEFT JOIN sections sec
                ON s.section_id = sec.id

            LEFT JOIN teachers t
                ON sec.teacher_id = t.id

            ORDER BY s.id ASC
        """)
        students = cur.fetchall()

        cur.execute("""
            SELECT rc.uid
            FROM rfid_cards rc

            LEFT JOIN students s
                ON s.uid = rc.uid

            WHERE s.uid IS NULL

            ORDER BY rc.id ASC
        """)
        available_uids = [row["uid"] for row in cur.fetchall()]

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
                                WHEN t.middle_name IS NOT NULL
                                     AND t.middle_name <> ''
                                THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS teacher_name

            FROM sections sec

            LEFT JOIN teachers t
                ON sec.teacher_id = t.id

            ORDER BY sec.section_name ASC
        """)
        sections = cur.fetchall()

        cur.execute("""
            SELECT
                id,
                first_name,
                middle_name,
                last_name,

                TRIM(
                    last_name || ', ' || first_name ||

                    CASE
                        WHEN middle_name IS NOT NULL
                             AND middle_name <> ''
                        THEN ' ' || middle_name
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
@login_required
def add_student():

    uid = request.form.get('uid', '').strip()
    existing_section_id = request.form.get('section_id', '').strip()
    new_section_name = request.form.get('new_section_name', '').strip()
    new_year_level = request.form.get('new_year_level', '').strip()
    new_teacher_id_raw = request.form.get('new_teacher_id', '').strip()
    last_name = request.form.get('last_name', '').strip()
    first_name = request.form.get('first_name', '').strip()
    middle_name = request.form.get('middle_name', '').strip()
    extension = request.form.get('extension', '').strip()
    birthday_raw = request.form.get('birthday', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    email = request.form.get('email', '').strip()
    schedule = request.form.get('schedule', '').strip()

    if (
        not last_name or
        not first_name or
        not birthday_raw or
        not contact_number or
        not email or
        not schedule
    ):
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('sregister.student'))

    try:
        birthday = normalize_birthday(birthday_raw)

        section_id = (
            int(existing_section_id)
            if existing_section_id else None
        )

        new_teacher_id = (
            int(new_teacher_id_raw)
            if new_teacher_id_raw else None
        )

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM students WHERE email = %s",
            (email,)
        )

        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id
            FROM students
            WHERE last_name = %s
            AND first_name = %s
            AND birthday = %s
        """, (last_name, first_name, birthday))

        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This student is already registered.", "error")
            return redirect(url_for('sregister.student'))

        if section_id is not None:
            cur.execute("""
                SELECT id
                FROM sections
                WHERE id = %s
            """, (section_id,))

            if not cur.fetchone():
                cur.close()
                conn.close()
                flash("Selected section does not exist.", "error")
                return redirect(url_for('sregister.student'))

        elif new_section_name:
            if new_teacher_id is not None:
                cur.execute("""
                    SELECT id
                    FROM teachers
                    WHERE id = %s
                """, (new_teacher_id,))

                if not cur.fetchone():
                    cur.close()
                    conn.close()
                    flash("Selected teacher does not exist.", "error")
                    return redirect(url_for('sregister.student'))

            cur.execute("""
                SELECT id
                FROM sections
                WHERE LOWER(section_name) = LOWER(%s)
                LIMIT 1
            """, (new_section_name,))

            existing = cur.fetchone()

            if existing:
                section_id = existing[0]
            else:
                cur.execute("""
                    INSERT INTO sections (
                        section_name,
                        year_level,
                        teacher_id
                    )
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (
                    new_section_name,
                    new_year_level if new_year_level else None,
                    new_teacher_id
                ))
                section_id = cur.fetchone()[0]

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
            cur.execute("""
                SELECT uid
                FROM rfid_cards
                WHERE uid = %s
            """, (uid,))

            if not cur.fetchone():
                cur.close()
                conn.close()
                flash("Selected UID does not exist.", "error")
                return redirect(url_for('sregister.student'))

            cur.execute("""
                SELECT id
                FROM students
                WHERE uid = %s
            """, (uid,))

            if cur.fetchone():
                cur.close()
                conn.close()
                flash("This UID is already linked to another student.", "error")
                return redirect(url_for('sregister.student'))

        cur.execute("""
            INSERT INTO students (
                uid,
                last_name,
                first_name,
                middle_name,
                extension,
                birthday,
                contact_number,
                email,
                schedule,
                section_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            uid,
            last_name,
            first_name,
            middle_name if middle_name else None,
            extension if extension else None,
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


@sregister.route('/update-student/<int:student_id>', methods=['POST'])
@login_required
def update_student(student_id):

    last_name = request.form.get('last_name', '').strip()
    first_name = request.form.get('first_name', '').strip()
    middle_name = request.form.get('middle_name', '').strip()
    extension = request.form.get('extension', '').strip()
    birthday_raw = request.form.get('birthday', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    email = request.form.get('email', '').strip()
    schedule = request.form.get('schedule', '').strip()
    section_id_raw = request.form.get('section_id', '').strip()

    if (
        not last_name or
        not first_name or
        not birthday_raw or
        not contact_number or
        not email or
        not schedule
    ):
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('sregister.student'))

    conn = None
    cur = None

    try:
        birthday = normalize_birthday(birthday_raw)
        section_id = int(section_id_raw) if section_id_raw else None

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id
            FROM students
            WHERE id = %s
        """, (student_id,))

        if not cur.fetchone():
            flash("Student not found.", "error")
            return redirect(url_for('sregister.student'))

        if section_id is not None:
            cur.execute("""
                SELECT id
                FROM sections
                WHERE id = %s
            """, (section_id,))

            if not cur.fetchone():
                flash("Selected section does not exist.", "error")
                return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id
            FROM students
            WHERE email = %s
            AND id <> %s
        """, (email, student_id))

        if cur.fetchone():
            flash("This email is already registered to another student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            UPDATE students
            SET
                last_name = %s,
                first_name = %s,
                middle_name = %s,
                extension = %s,
                birthday = %s,
                contact_number = %s,
                email = %s,
                schedule = %s,
                section_id = %s
            WHERE id = %s
        """, (
            last_name,
            first_name,
            middle_name if middle_name else None,
            extension if extension else None,
            birthday,
            contact_number,
            email,
            schedule,
            section_id,
            student_id
        ))

        conn.commit()

        flash("Student updated successfully.", "success")
        return redirect(url_for('sregister.student'))

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error updating student: {str(e)}", "error")
        return redirect(url_for('sregister.student'))

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
            

@sregister.route('/import-excel', methods=['POST'])
@login_required
def import_excel():
    from flask import jsonify
    data = request.get_json()
    students = data.get('students', [])

    if not students:
        return jsonify({'message': 'No students provided.'}), 400

    conn = None
    cur = None
    imported = 0
    skipped = 0
    no_uid_available = False

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # FIRST: Check if there are enough available UIDs
        cur.execute("""
            SELECT COUNT(rc.uid) as available_uids
            FROM rfid_cards rc
            LEFT JOIN students s ON s.uid = rc.uid
            WHERE s.uid IS NULL
        """)
        available_count = cur.fetchone()[0]
        
        # Count valid students to import (excluding duplicates)
        valid_students_count = 0
        for s in students:
            try:
                last_name = (s.get('last_name') or '').strip()
                first_name = (s.get('first_name') or '').strip()
                birthday_raw = (s.get('birthday') or '').strip()
                email = (s.get('email') or '').strip()
                
                if not (last_name and first_name and birthday_raw and email):
                    continue
                
                # Check if email already exists
                cur.execute("SELECT id FROM students WHERE email = %s", (email,))
                if cur.fetchone():
                    continue
                    
                valid_students_count += 1
            except Exception:
                continue
        
        if valid_students_count > available_count:
            cur.close()
            conn.close()
            return jsonify({
                'message': f'❌ Import failed: Not enough available UIDs. Need {valid_students_count} UIDs but only {available_count} available. Please add more RFID cards first.'
            }), 400
        
        # Process each student
        for s in students:
            try:
                last_name     = (s.get('last_name') or '').strip()
                first_name    = (s.get('first_name') or '').strip()
                middle_name   = (s.get('middle_name') or '').strip() or None
                extension     = (s.get('extension') or '').strip() or None
                birthday_raw  = (s.get('birthday') or '').strip()
                contact       = (s.get('contact_number') or '').strip()
                email         = (s.get('email') or '').strip()
                schedule      = (s.get('schedule') or '').strip()
                section_name  = (s.get('section_name') or '').strip()
                year_level    = (s.get('year_level') or '').strip() or None
                
                # Validate required fields
                if not (last_name and first_name and birthday_raw and contact and email and schedule):
                    skipped += 1
                    continue

                birthday = normalize_birthday(birthday_raw)

                # Check for duplicate email
                cur.execute("SELECT id FROM students WHERE email = %s", (email,))
                if cur.fetchone():
                    skipped += 1
                    continue
                
                # Check for duplicate student (same name and birthday)
                cur.execute("""
                    SELECT id FROM students 
                    WHERE last_name = %s AND first_name = %s AND birthday = %s
                """, (last_name, first_name, birthday))
                if cur.fetchone():
                    skipped += 1
                    continue

                # Resolve or create section
                section_id = None
                if section_name:
                    cur.execute("SELECT id FROM sections WHERE LOWER(section_name) = LOWER(%s) LIMIT 1", (section_name,))
                    row = cur.fetchone()
                    if row:
                        section_id = row[0]
                    else:
                        cur.execute(
                            "INSERT INTO sections (section_name, year_level) VALUES (%s, %s) RETURNING id",
                            (section_name, year_level)
                        )
                        section_id = cur.fetchone()[0]

                # Get available UID
                cur.execute("""
                    SELECT rc.uid FROM rfid_cards rc
                    LEFT JOIN students s ON s.uid = rc.uid
                    WHERE s.uid IS NULL
                    ORDER BY rc.id ASC LIMIT 1
                """)
                uid_row = cur.fetchone()
                
                # This should always have a value since we checked count above
                if not uid_row:
                    no_uid_available = True
                    break
                    
                uid = uid_row[0]

                # Insert the student
                cur.execute("""
                    INSERT INTO students
                        (uid, last_name, first_name, middle_name, extension,
                         birthday, contact_number, email, schedule, section_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (uid, last_name, first_name, middle_name, extension,
                      birthday, contact, email, schedule, section_id))

                imported += 1

            except Exception as e:
                print(f"Error processing student: {str(e)}")
                skipped += 1
                continue
        
        if no_uid_available:
            conn.rollback()
            return jsonify({'message': f'❌ Import failed: No available UIDs found during import process.'}), 400

        conn.commit()
        
        if imported > 0:
            return jsonify({'message': f'✅ Successfully imported {imported} students. Skipped {skipped} duplicate/invalid rows.'})
        else:
            return jsonify({'message': f'⚠️ No students were imported. Skipped {skipped} duplicate/invalid rows. No available UIDs or all students were duplicates.'}), 400

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'message': f'❌ Import failed: {str(e)}'}), 500

    finally:
        if cur: 
            cur.close()
        if conn: 
            conn.close()