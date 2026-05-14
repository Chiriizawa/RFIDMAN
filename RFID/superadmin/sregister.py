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
# SF1 NAME PARSER
# ─────────────────────────────────────────────

def parse_sf1_name(raw):
    """
    Parse a name string in SF1 format: "LASTNAME,FIRSTNAME [MIDDLENAME]"
    Returns (last_name, first_name, middle_name) — all title-cased strings.
    """
    raw = (raw or '').strip()
    if not raw:
        return None, None, None

    if ',' in raw:
        last, rest = raw.split(',', 1)
        last = last.strip().title()
        rest = rest.strip()
        parts = rest.split()
        if len(parts) >= 2:
            middle = parts[-1].title()
            first  = ' '.join(parts[:-1]).title()
        elif len(parts) == 1:
            first  = parts[0].title()
            middle = ''
        else:
            first  = ''
            middle = ''
    else:
        last   = raw.title()
        first  = ''
        middle = ''

    return last, first, middle


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

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

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

        cur.close()
        conn.close()

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")

    return render_template(
        "superadmin/student.html",
        students=students,
    )


@sregister.route('/add-student', methods=['POST'])
@login_required
def add_student():
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

        # ── Cross-table checks: block teachers from being added as students ──
        cur.execute("SELECT id FROM teachers WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This email is already registered to a teacher. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("SELECT id FROM teachers WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This contact number is already registered to a teacher. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id FROM teachers
            WHERE last_name = %s AND first_name = %s
        """, (last_name, first_name))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("A teacher with the same name is already registered. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        # ── Within-table duplicate checks ────────────────────────────────────
        cur.execute("SELECT id FROM students WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("SELECT id FROM students WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This contact number is already registered.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s AND first_name = %s AND birthday = %s
        """, (last_name, first_name, birthday))
        if cur.fetchone():
            cur.close(); conn.close()
            flash("This student is already registered.", "error")
            return redirect(url_for('sregister.student'))

        # ── Insert ────────────────────────────────────────────────────────────
        cur.execute("""
            INSERT INTO students
                (uid, last_name, first_name, middle_name, extension,
                 birthday, contact_number, email)
            VALUES (NULL, %s, %s, %s, %s, %s, %s, %s)
        """, (
            last_name, first_name,
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

        # ── Cross-table checks: block teacher data from being used by a student ──
        cur.execute("SELECT id FROM teachers WHERE email = %s", (email,))
        if cur.fetchone():
            flash("This email is already registered to a teacher. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("SELECT id FROM teachers WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            flash("This contact number is already registered to a teacher. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id FROM teachers
            WHERE last_name = %s AND first_name = %s
        """, (last_name, first_name))
        if cur.fetchone():
            flash("A teacher with the same name is already registered. A teacher cannot also be a student.", "error")
            return redirect(url_for('sregister.student'))

        # ── Within-table duplicate checks ────────────────────────────────────
        cur.execute("SELECT id FROM students WHERE email = %s AND id <> %s", (email, student_id))
        if cur.fetchone():
            flash("This email is already registered to another student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("SELECT id FROM students WHERE contact_number = %s AND id <> %s", (contact_number, student_id))
        if cur.fetchone():
            flash("This contact number is already registered to another student.", "error")
            return redirect(url_for('sregister.student'))

        cur.execute("""
            SELECT id FROM students
            WHERE last_name = %s AND first_name = %s AND birthday = %s AND id <> %s
        """, (last_name, first_name, birthday, student_id))
        if cur.fetchone():
            flash("Another student with the same name and birthday already exists.", "error")
            return redirect(url_for('sregister.student'))

        # ── Update ────────────────────────────────────────────────────────────
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


@sregister.route('/assign-section/<int:student_id>', methods=['POST'])
@login_required
def assign_section(student_id):
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

        cur.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if not cur.fetchone():
            return jsonify({'ok': False, 'message': 'Student not found.'}), 404

        if section_id:
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

            cur.execute("""
                UPDATE students SET section_id = %s WHERE id = %s
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
            cur.execute("UPDATE students SET section_id = NULL WHERE id = %s", (student_id,))
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

    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        for s in students:
            try:
                last_name    = (s.get('last_name') or '').strip()
                first_name   = (s.get('first_name') or '').strip()
                middle_name  = (s.get('middle_name') or '').strip() or None
                extension    = (s.get('extension') or '').strip() or None
                birthday_raw = (s.get('birthday') or '').strip()
                contact      = (s.get('contact_number') or '').strip()
                email        = (s.get('email') or '').strip()

                # Require at minimum: name + birthday
                if not (last_name and first_name and birthday_raw):
                    skipped += 1
                    continue

                # Validate name characters
                if not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", last_name):
                    skipped += 1
                    continue
                if not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", first_name):
                    skipped += 1
                    continue
                if middle_name and not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", middle_name):
                    skipped += 1
                    continue

                # Validate birthday
                try:
                    birthday = normalize_birthday(birthday_raw)
                except ValueError:
                    skipped += 1
                    continue

                today = date.today()
                if birthday >= today or birthday.year >= today.year or birthday.year < MIN_BIRTH_YEAR:
                    skipped += 1
                    continue

                # Validate contact + email only when provided
                if contact:
                    if not contact.isdigit() or len(contact) != 11 or not contact.startswith('09'):
                        skipped += 1
                        continue
                if email:
                    if not re.match(r'^[^\s@]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
                        skipped += 1
                        continue

                # ── Cross-table checks: skip if person is already a teacher ──
                if email:
                    cur.execute("SELECT id FROM teachers WHERE email = %s", (email,))
                    if cur.fetchone():
                        skipped += 1
                        continue

                if contact:
                    cur.execute("SELECT id FROM teachers WHERE contact_number = %s", (contact,))
                    if cur.fetchone():
                        skipped += 1
                        continue

                cur.execute("""
                    SELECT id FROM teachers
                    WHERE last_name = %s AND first_name = %s
                """, (last_name, first_name))
                if cur.fetchone():
                    skipped += 1
                    continue

                # ── Within-table duplicate checks ─────────────────────────────
                if email:
                    cur.execute("SELECT id FROM students WHERE email = %s", (email,))
                    if cur.fetchone():
                        skipped += 1
                        continue
                if contact:
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

                # ── Insert ────────────────────────────────────────────────────
                cur.execute("""
                    INSERT INTO students
                        (uid, last_name, first_name, middle_name, extension,
                         birthday, contact_number, email)
                    VALUES (NULL, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    last_name, first_name, middle_name, extension,
                    birthday, contact or None, email or None
                ))

                imported += 1

            except Exception as e:
                print(f"Error processing student: {str(e)}")
                skipped += 1
                continue

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