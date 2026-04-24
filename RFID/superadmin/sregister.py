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

        # Available RFID cards not linked to students
        cur.execute("""
            SELECT rc.uid
            FROM rfid_cards rc

            LEFT JOIN students s
                ON s.uid = rc.uid

            WHERE s.uid IS NULL

            ORDER BY rc.id ASC
        """)
        available_uids = [row["uid"] for row in cur.fetchall()]

        # Sections
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

        # Teachers
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
def add_student():

    uid = request.form.get('uid', '').strip()

    existing_section_id = request.form.get(
        'section_id',
        ''
    ).strip()

    new_section_name = request.form.get(
        'new_section_name',
        ''
    ).strip()

    new_year_level = request.form.get(
        'new_year_level',
        ''
    ).strip()

    new_teacher_id_raw = request.form.get(
        'new_teacher_id',
        ''
    ).strip()

    last_name = request.form.get(
        'last_name',
        ''
    ).strip()

    first_name = request.form.get(
        'first_name',
        ''
    ).strip()

    middle_name = request.form.get(
        'middle_name',
        ''
    ).strip()

    extension = request.form.get(
        'extension',
        ''
    ).strip()

    birthday_raw = request.form.get(
        'birthday',
        ''
    ).strip()

    contact_number = request.form.get(
        'contact_number',
        ''
    ).strip()

    email = request.form.get(
        'email',
        ''
    ).strip()

    schedule = request.form.get(
        'schedule',
        ''
    ).strip()

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

        # duplicate email
        cur.execute(
            "SELECT id FROM students WHERE email = %s",
            (email,)
        )

        if cur.fetchone():
            cur.close()
            conn.close()

            flash(
                "This email is already registered.",
                "error"
            )

            return redirect(url_for('sregister.student'))

        # duplicate student
        cur.execute("""
            SELECT id
            FROM students

            WHERE last_name = %s
            AND first_name = %s
            AND birthday = %s
        """, (
            last_name,
            first_name,
            birthday
        ))

        if cur.fetchone():
            cur.close()
            conn.close()

            flash(
                "This student is already registered.",
                "error"
            )

            return redirect(url_for('sregister.student'))

        # existing section
        if section_id is not None:

            cur.execute("""
                SELECT id
                FROM sections
                WHERE id = %s
            """, (section_id,))

            if not cur.fetchone():
                cur.close()
                conn.close()

                flash(
                    "Selected section does not exist.",
                    "error"
                )

                return redirect(url_for('sregister.student'))

        # create section
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

                    flash(
                        "Selected teacher does not exist.",
                        "error"
                    )

                    return redirect(url_for('sregister.student'))

            cur.execute("""
                SELECT id
                FROM sections

                WHERE LOWER(section_name)
                = LOWER(%s)

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

        # auto assign UID
        if not uid:

            cur.execute("""
                SELECT rc.uid

                FROM rfid_cards rc

                LEFT JOIN students s
                    ON s.uid = rc.uid

                WHERE s.uid IS NULL

                ORDER BY rc.id ASC

                LIMIT 1
            """)

            uid_row = cur.fetchone()

            if not uid_row:
                cur.close()
                conn.close()

                flash(
                    "No available UID found.",
                    "error"
                )

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

                flash(
                    "Selected UID does not exist.",
                    "error"
                )

                return redirect(url_for('sregister.student'))

            cur.execute("""
                SELECT id
                FROM students
                WHERE uid = %s
            """, (uid,))

            if cur.fetchone():
                cur.close()
                conn.close()

                flash(
                    "This UID is already linked to another student.",
                    "error"
                )

                return redirect(url_for('sregister.student'))

        # insert student
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

            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
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

        flash(
            "Student added successfully.",
            "success"
        )

        return redirect(url_for('sregister.student'))

    except Exception as e:

        flash(
            f"Error adding student: {str(e)}",
            "error"
        )

        return redirect(url_for('sregister.student'))


# =========================
# ADD LANG ITO SA DULO
# =========================
@sregister.route('/update-student/<int:student_id>', methods=['POST'])
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