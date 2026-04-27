from flask import Blueprint, render_template, flash, request, redirect, url_for, jsonify, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import re
from datetime import datetime, timedelta, date

load_dotenv()

sregister = Blueprint("sregister", __name__, template_folder="template")

VALID_EXTENSIONS = {'', 'Jr.', 'Sr.', 'II', 'III', 'IV', 'V'}
MIN_BIRTH_YEAR   = 2006


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


def validate_student_fields(last_name, first_name, middle_name, extension,
                            birthday_raw, contact_number, email):
    """
    Returns a list of error strings. Empty list = all valid.
    """
    errors = []
    today  = date.today()

    # ── Last Name ─────────────────────────────────
    if not last_name:
        errors.append("Last name is required.")
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", last_name):
        errors.append("Last name must contain letters only.")
    elif len(last_name) < 3:
        errors.append("Last name must be at least 3 characters.")

    # ── First Name ────────────────────────────────
    if not first_name:
        errors.append("First name is required.")
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", first_name):
        errors.append("First name must contain letters only.")
    elif len(first_name) < 3:
        errors.append("First name must be at least 3 characters.")

    # ── Middle Name (optional, min 2 if provided) ─
    if middle_name:
        if not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", middle_name):
            errors.append("Middle name must contain letters only.")
        elif len(middle_name) < 2:
            errors.append("Middle name must be at least 2 characters.")

    # ── Extension ─────────────────────────────────
    if extension and extension not in VALID_EXTENSIONS:
        errors.append(f"Extension must be one of: Jr., Sr., II, III, IV, V.")

    # ── Birthday ──────────────────────────────────
    if not birthday_raw:
        errors.append("Birthday is required.")
    else:
        try:
            birthday = normalize_birthday(birthday_raw)
            if birthday >= today:
                errors.append("Birthday cannot be today or a future date.")
            elif birthday.year >= today.year:
                errors.append(f"Birth year cannot be the current year ({today.year}).")
            elif birthday.year < MIN_BIRTH_YEAR:
                errors.append(f"Birth year must be {MIN_BIRTH_YEAR} or later.")
        except ValueError as e:
            errors.append(str(e))

    # ── Contact Number ────────────────────────────
    if not contact_number:
        errors.append("Contact number is required.")
    elif not contact_number.isdigit():
        errors.append("Contact number must contain digits only.")
    elif len(contact_number) != 11:
        errors.append(f"Contact number must be exactly 11 digits (got {len(contact_number)}).")
    elif not contact_number.startswith('09'):
        errors.append("Contact number must start with 09.")

    # ── Email ─────────────────────────────────────
    ALLOWED_DOMAINS = {
        'gmail.com', 'yahoo.com', 'yahoo.com.ph',
        'outlook.com', 'hotmail.com', 'live.com',
        'icloud.com', 'me.com', 'mac.com',
        'protonmail.com', 'proton.me',
        'aol.com', 'zoho.com',
        'deped.gov.ph', 'ched.gov.ph', 'edu.ph', 'school.edu.ph',
        'up.edu.ph', 'dlsu.edu.ph', 'ateneo.edu.ph', 'ust.edu.ph',
        'admu.edu.ph', 'mapua.edu.ph', 'pup.edu.ph', 'tip.edu.ph',
        'feu.edu.ph', 'nu.edu.ph', 'ceu.edu.ph', 'slu.edu.ph',
        'au.edu.ph', 'usc.edu.ph', 'usjr.edu.ph', 'cpu.edu.ph',
        'wvsu.edu.ph', 'vsu.edu.ph',
    }

    def is_valid_email_domain(addr):
        if '@' not in addr:
            return False
        domain = addr.lower().split('@')[-1]
        if domain in ALLOWED_DOMAINS:
            return True
        if re.match(r'^[a-z0-9\-]+\.edu\.ph$', domain):
            return True
        if re.match(r'^[a-z0-9\-]+\.gov\.ph$', domain):
            return True
        return False

    if not email:
        errors.append("Email address is required.")
    elif not re.match(r'^[^\s@]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        errors.append("Please enter a valid email address.")
    elif not is_valid_email_domain(email):
        domain_part = email.split('@')[-1] if '@' in email else ''
        errors.append(f'"{domain_part}" is not an accepted email domain. Use Gmail, Yahoo, Outlook, iCloud, or a valid school/government email.')

    return errors


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise Exception("❌ DATABASE_URL not found in .env")
    return psycopg2.connect(database_url.strip(), sslmode="require")


# ─────────────────────────────────────────────
# SCHEDULE SUBQUERY HELPER
# Aggregates all schedule rows for a section into one multi-line string.
# Format per row: "Monday | General Chemistry 1 | 7:00-8:00 AM"
# ─────────────────────────────────────────────

SCHEDULE_SUBQUERY = """
    (
        SELECT STRING_AGG(
            sch.day || ' | ' || sch.subject || ' | ' || sch.time,
            E'\\n'
            ORDER BY sch.day, sch.time
        )
        FROM schedules sch
        WHERE sch.section_id = sec.id
    )
"""


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

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # ── Students with section + auto-resolved schedule from schedules table ──
        cur.execute(f"""
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
                s.created_at,
                s.section_id,
                sec.section_name,
                sec.year_level,
                -- Auto-fill schedule by aggregating from schedules table
                {SCHEDULE_SUBQUERY} AS schedule,
                CASE
                    WHEN t.id IS NOT NULL THEN
                        TRIM(
                            COALESCE(t.last_name, '') || ', ' ||
                            COALESCE(t.first_name, '') ||
                            CASE
                                WHEN t.middle_name IS NOT NULL AND t.middle_name <> ''
                                THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS teacher_name
            FROM students s
            LEFT JOIN sections sec ON s.section_id = sec.id
            LEFT JOIN teachers t   ON sec.teacher_id = t.id
            ORDER BY s.id ASC
        """)
        students = cur.fetchall()

        # ── Available UIDs for add form ──────────────────────────────────────
        cur.execute("""
            SELECT rc.uid
            FROM rfid_cards rc
            LEFT JOIN students s ON s.uid = rc.uid
            WHERE s.uid IS NULL
            ORDER BY rc.id ASC
        """)
        available_uids = [row["uid"] for row in cur.fetchall()]

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
@login_required
def add_student():
    uid            = request.form.get('uid', '').strip()
    last_name      = request.form.get('last_name', '').strip()
    first_name     = request.form.get('first_name', '').strip()
    middle_name    = request.form.get('middle_name', '').strip()
    extension      = request.form.get('extension', '').strip()
    birthday_raw   = request.form.get('birthday', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    email          = request.form.get('email', '').strip()

    errors = validate_student_fields(
        last_name, first_name, middle_name, extension,
        birthday_raw, contact_number, email
    )
    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(url_for('sregister.student'))

    try:
        birthday = normalize_birthday(birthday_raw)

        conn = get_db_connection()
        cur  = conn.cursor()

        # Duplicate email check
        cur.execute("SELECT id FROM students WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate contact number check
        cur.execute("SELECT id FROM students WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This contact number is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate student check (name + birthday)
        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s AND first_name = %s AND birthday = %s
        """, (last_name, first_name, birthday))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This student is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # UID handling
        if not uid:
            cur.execute("""
                SELECT rc.uid FROM rfid_cards rc
                LEFT JOIN students s ON s.uid = rc.uid
                WHERE s.uid IS NULL
                ORDER BY rc.id ASC LIMIT 1
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
                flash("Selected UID does not exist.", "error")
                return redirect(url_for('sregister.student'))

            cur.execute("SELECT id FROM students WHERE uid = %s", (uid,))
            if cur.fetchone():
                cur.close(); conn.close()
                flash("This UID is already linked to another student.", "error")
                return redirect(url_for('sregister.student'))

        # INSERT — no section at creation; schedule comes from schedules table via section
        cur.execute("""
            INSERT INTO students
                (uid, last_name, first_name, middle_name, extension,
                 birthday, contact_number, email)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            uid, last_name, first_name,
            middle_name or None, extension or None,
            birthday, contact_number, email
        ))

        conn.commit()
        cur.close(); conn.close()
        flash("Student added successfully.", "success")
        return redirect(url_for('sregister.student'))

    except Exception as e:
        flash(f"Error adding student: {str(e)}", "error")
        return redirect(url_for('sregister.student'))


@sregister.route('/update-student/<int:student_id>', methods=['POST'])
@login_required
def update_student(student_id):
    last_name      = request.form.get('last_name', '').strip()
    first_name     = request.form.get('first_name', '').strip()
    middle_name    = request.form.get('middle_name', '').strip()
    extension      = request.form.get('extension', '').strip()
    birthday_raw   = request.form.get('birthday', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    email          = request.form.get('email', '').strip()

    errors = validate_student_fields(
        last_name, first_name, middle_name, extension,
        birthday_raw, contact_number, email
    )
    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(url_for('sregister.student'))

    conn = None
    cur  = None

    try:
        birthday = normalize_birthday(birthday_raw)

        conn = get_db_connection()
        cur  = conn.cursor()

        cur.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if not cur.fetchone():
            flash("Student not found.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate email check (exclude self)
        cur.execute("SELECT id FROM students WHERE email = %s AND id <> %s", (email, student_id))
        if cur.fetchone():
            flash("This email is already registered to another student.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate contact number check (exclude self)
        cur.execute("SELECT id FROM students WHERE contact_number = %s AND id <> %s", (contact_number, student_id))
        if cur.fetchone():
            flash("This contact number is already registered to another student.", "error")
            return redirect(url_for('sregister.student'))

        # Duplicate name + birthday check (exclude self)
        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s AND first_name = %s AND birthday = %s AND id <> %s
        """, (last_name, first_name, birthday, student_id))
        if cur.fetchone():
            flash("Another student with the same name and birthday already exists.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            UPDATE students
            SET
                last_name      = %s,
                first_name     = %s,
                middle_name    = %s,
                extension      = %s,
                birthday       = %s,
                contact_number = %s,
                email          = %s
            WHERE id = %s
        """, (
            last_name, first_name,
            middle_name or None, extension or None,
            birthday, contact_number, email,
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
        if cur:  cur.close()
        if conn: conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ROUTE: Assign / unassign a student to a section.
# Schedule is read live from the schedules table — no schedule column needed
# on students. section_id on the student row is enough.
# ─────────────────────────────────────────────────────────────────────────────

@sregister.route('/assign-section/<int:student_id>', methods=['POST'])
@login_required
def assign_section(student_id):
    """
    Assign (or clear) a student's section.

    Accepts JSON  { "section_id": <int|null> }
    or form data  section_id=<int|"">

    On success returns JSON { "ok": true, "schedule": "...", "section_name": "...",
                              "year_level": "...", "teacher_name": "..." }
    """
    # Support both JSON and form-encoded bodies
    if request.is_json:
        payload    = request.get_json(silent=True) or {}
        section_id = payload.get('section_id') or None
    else:
        raw = request.form.get('section_id', '').strip()
        section_id = int(raw) if raw.isdigit() else None

    conn = None
    cur  = None

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        # Verify student exists
        cur.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if not cur.fetchone():
            return jsonify({'ok': False, 'message': 'Student not found.'}), 404

        if section_id:
            # Fetch the section + aggregate schedule from schedules table
            cur.execute(f"""
                SELECT
                    sec.id,
                    sec.section_name,
                    sec.year_level,
                    {SCHEDULE_SUBQUERY} AS schedule,
                    CASE
                        WHEN t.id IS NOT NULL THEN
                            TRIM(
                                COALESCE(t.last_name, '') || ', ' ||
                                COALESCE(t.first_name, '') ||
                                CASE
                                    WHEN t.middle_name IS NOT NULL AND t.middle_name <> ''
                                    THEN ' ' || t.middle_name
                                    ELSE ''
                                END
                            )
                        ELSE NULL
                    END AS teacher_name
                FROM sections sec
                LEFT JOIN teachers t ON sec.teacher_id = t.id
                WHERE sec.id = %s
            """, (section_id,))
            section = cur.fetchone()

            if not section:
                return jsonify({'ok': False, 'message': 'Section not found.'}), 404

            # Update student: set section_id only (schedule is derived live)
            cur.execute("""
                UPDATE students
                SET section_id = %s
                WHERE id = %s
            """, (section_id, student_id))

            conn.commit()
            return jsonify({
                'ok':           True,
                'section_id':   section_id,
                'section_name': section['section_name'] or '—',
                'year_level':   section['year_level']   or '—',
                'teacher_name': section['teacher_name'] or '—',
                'schedule':     section['schedule']     or '—',
            })

        else:
            # Clear section assignment
            cur.execute("""
                UPDATE students
                SET section_id = NULL
                WHERE id = %s
            """, (student_id,))
            conn.commit()
            return jsonify({
                'ok':           True,
                'section_id':   None,
                'section_name': '—',
                'year_level':   '—',
                'teacher_name': '—',
                'schedule':     '—',
            })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'ok': False, 'message': str(e)}), 500

    finally:
        if cur:  cur.close()
        if conn: conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ROUTE: Return all sections as JSON (used by the Assign Section modal)
# ─────────────────────────────────────────────────────────────────────────────

@sregister.route('/sections-list', methods=['GET'])
@login_required
def sections_list():
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"""
            SELECT
                sec.id,
                sec.section_name,
                sec.year_level,
                {SCHEDULE_SUBQUERY} AS schedule,
                CASE
                    WHEN t.id IS NOT NULL THEN
                        TRIM(
                            COALESCE(t.last_name, '') || ', ' ||
                            COALESCE(t.first_name, '') ||
                            CASE
                                WHEN t.middle_name IS NOT NULL AND t.middle_name <> ''
                                THEN ' ' || t.middle_name
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
        cur.close()
        conn.close()
        return jsonify({'ok': True, 'sections': sections})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


@sregister.route('/import-excel', methods=['POST'])
@login_required
def import_excel():
    data     = request.get_json()
    students = data.get('students', [])

    if not students:
        return jsonify({'message': 'No students provided.'}), 400

    conn = None
    cur  = None
    imported = 0
    skipped  = 0
    no_uid_available = False

    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        # Count available UIDs
        cur.execute("""
            SELECT COUNT(rc.uid)
            FROM rfid_cards rc
            LEFT JOIN students s ON s.uid = rc.uid
            WHERE s.uid IS NULL
        """)
        available_count = cur.fetchone()[0]

        # Count valid students
        valid_students_count = 0
        for s in students:
            try:
                last_name    = (s.get('last_name') or '').strip()
                first_name   = (s.get('first_name') or '').strip()
                middle_name  = (s.get('middle_name') or '').strip()
                extension    = (s.get('extension') or '').strip()
                birthday_raw = (s.get('birthday') or '').strip()
                contact      = (s.get('contact_number') or '').strip()
                email        = (s.get('email') or '').strip()

                if not (last_name and first_name and birthday_raw and contact and email):
                    continue
                field_errors = validate_student_fields(
                    last_name, first_name, middle_name, extension,
                    birthday_raw, contact, email
                )
                if field_errors:
                    continue
                cur.execute("SELECT id FROM students WHERE email = %s", (email,))
                if cur.fetchone():
                    continue
                cur.execute("SELECT id FROM students WHERE contact_number = %s", (contact,))
                if cur.fetchone():
                    continue
                valid_students_count += 1
            except Exception:
                continue

        if valid_students_count > available_count:
            cur.close(); conn.close()
            return jsonify({
                'message': (
                    f'❌ Import failed: Not enough available UIDs. '
                    f'Need {valid_students_count} UIDs but only {available_count} available. '
                    f'Please add more RFID cards first.'
                )
            }), 400

        for s in students:
            try:
                last_name    = (s.get('last_name') or '').strip()
                first_name   = (s.get('first_name') or '').strip()
                middle_name  = (s.get('middle_name') or '').strip() or None
                extension    = (s.get('extension') or '').strip() or None
                birthday_raw = (s.get('birthday') or '').strip()
                contact      = (s.get('contact_number') or '').strip()
                email        = (s.get('email') or '').strip()

                if not (last_name and first_name and birthday_raw and contact and email):
                    skipped += 1
                    continue

                field_errors = validate_student_fields(
                    last_name, first_name, middle_name or '', extension or '',
                    birthday_raw, contact, email
                )
                if field_errors:
                    skipped += 1
                    continue

                birthday = normalize_birthday(birthday_raw)

                # Duplicate checks
                cur.execute("SELECT id FROM students WHERE email = %s", (email,))
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute("SELECT id FROM students WHERE contact_number = %s", (contact,))
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute("""
                    SELECT id FROM students
                    WHERE last_name = %s AND first_name = %s AND birthday = %s
                """, (last_name, first_name, birthday))
                if cur.fetchone():
                    skipped += 1
                    continue

                # Assign next available UID
                cur.execute("""
                    SELECT rc.uid FROM rfid_cards rc
                    LEFT JOIN students s ON s.uid = rc.uid
                    WHERE s.uid IS NULL
                    ORDER BY rc.id ASC LIMIT 1
                """)
                uid_row = cur.fetchone()
                if not uid_row:
                    no_uid_available = True
                    break
                uid = uid_row[0]

                # INSERT — section assigned separately via assign-section route
                cur.execute("""
                    INSERT INTO students
                        (uid, last_name, first_name, middle_name, extension,
                         birthday, contact_number, email)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (uid, last_name, first_name, middle_name, extension,
                      birthday, contact, email))

                imported += 1

            except Exception as e:
                print(f"Error processing student: {str(e)}")
                skipped += 1
                continue

        if no_uid_available:
            conn.rollback()
            return jsonify({'message': '❌ Import failed: No available UIDs found during import process.'}), 400

        conn.commit()

        if imported > 0:
            return jsonify({'message': f'✅ Successfully imported {imported} students. Skipped {skipped} duplicate/invalid rows.'})
        else:
            return jsonify({'message': f'⚠️ No students were imported. Skipped {skipped} duplicate/invalid rows.'}), 400

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'message': f'❌ Import failed: {str(e)}'}), 500

    finally:
        if cur:  cur.close()
        if conn: conn.close()