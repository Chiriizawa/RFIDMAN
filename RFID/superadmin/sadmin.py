from flask import Blueprint, render_template, request, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

load_dotenv()

sadmin = Blueprint("sadmin", __name__, template_folder="template")


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432),
        sslmode="require"
    )


@sadmin.route('/')
def index():
    total_teachers = 0
    total_students = 0
    total_rfid_cards = 0

    present_today = 0
    late_today = 0
    absent_today = 0
    attendance_rate = 0

    unlinked_rfid = 0
    students_without_section = 0
    sections_without_teacher = 0

    recent_activities = []
    teachers_overview = []

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Total teachers
        cur.execute("SELECT COUNT(*) AS count FROM teachers")
        total_teachers = cur.fetchone()["count"]

        # Total students
        cur.execute("SELECT COUNT(*) AS count FROM students")
        total_students = cur.fetchone()["count"]

        # Total RFID cards
        cur.execute("SELECT COUNT(*) AS count FROM rfid_cards")
        total_rfid_cards = cur.fetchone()["count"]

        # Unlinked RFID cards
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM rfid_cards r
            LEFT JOIN students s ON r.uid = s.uid
            WHERE s.uid IS NULL
        """)
        unlinked_rfid = cur.fetchone()["count"]

        # Students without section
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM students
            WHERE section_id IS NULL
        """)
        students_without_section = cur.fetchone()["count"]

        # Sections without teacher
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM sections
            WHERE teacher_id IS NULL
        """)
        sections_without_teacher = cur.fetchone()["count"]

        # Recent activity
        cur.execute("""
            SELECT
                CONCAT(
                    COALESCE(s.first_name, ''), 
                    CASE WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> '' THEN ' ' || s.middle_name ELSE '' END,
                    ' ',
                    COALESCE(s.last_name, ''),
                    CASE WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> '' THEN ' ' || s.extension ELSE '' END
                ) AS student_name,
                s.uid,
                TO_CHAR(s.created_at, 'HH12:MI AM') AS time,
                'Registered' AS status
            FROM students s
            WHERE s.uid IS NOT NULL
            ORDER BY s.created_at DESC
            LIMIT 10
        """)
        rows = cur.fetchall()

        recent_activities = [
            {
                "student_name": row["student_name"],
                "uid": row["uid"],
                "time": row["time"],
                "status": row["status"]
            }
            for row in rows
        ]

        # Teachers overview
        cur.execute("""
            SELECT
                t.id,
                CONCAT(
                    COALESCE(t.first_name, ''),
                    CASE WHEN t.middle_name IS NOT NULL AND TRIM(t.middle_name) <> '' THEN ' ' || t.middle_name ELSE '' END,
                    ' ',
                    COALESCE(t.last_name, ''),
                    CASE WHEN t.extension IS NOT NULL AND TRIM(t.extension) <> '' THEN ' ' || t.extension ELSE '' END
                ) AS full_name,
                t.email,
                t.contact_number,
                TO_CHAR(t.created_at, 'YYYY-MM-DD HH12:MI AM') AS created_at
            FROM teachers t
            ORDER BY t.created_at DESC
            LIMIT 10
        """)
        teacher_rows = cur.fetchall()

        teachers_overview = [
            {
                "id": row["id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "contact_number": row["contact_number"],
                "created_at": row["created_at"]
            }
            for row in teacher_rows
        ]

    except Exception as e:
        print("Superadmin dashboard error:", e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template(
        'superadmin/index.html',
        total_teachers=total_teachers,
        total_students=total_students,
        total_rfid_cards=total_rfid_cards,
        present_today=present_today,
        late_today=late_today,
        absent_today=absent_today,
        attendance_rate=attendance_rate,
        unlinked_rfid=unlinked_rfid,
        students_without_section=students_without_section,
        sections_without_teacher=sections_without_teacher,
        recent_activities=recent_activities,
        teachers_overview=teachers_overview
    )


@sadmin.route('/UID')
def uid():
    conn = None
    cur = None
    uids = []

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                r.id,
                r.uid,
                r.created_at,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM students s
                        WHERE s.uid = r.uid
                    ) THEN 'Used'
                    ELSE 'Available'
                END AS status
            FROM rfid_cards r
            ORDER BY r.id DESC
        """)
        uids = cur.fetchall()

    except Exception as e:
        print("RFID cards error:", e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template("superadmin/uid.html", uids=uids)


@sadmin.route('/Login')
def login():
    return render_template("superadmin/login.html")


@sadmin.route('/attendance')
def attendance():
    selected_section = request.args.get("section", "").strip()

    conn = None
    cur = None
    sections = []
    attendance_rows = []

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # get all sections
        cur.execute("""
            SELECT id, section_name
            FROM sections
            ORDER BY section_name ASC
        """)
        sections = cur.fetchall()

        # attendance with section filter
        if selected_section:
            cur.execute("""
                SELECT
                    a.id,
                    CONCAT(
                        COALESCE(s.first_name, ''),
                        CASE WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> '' THEN ' ' || s.middle_name ELSE '' END,
                        ' ',
                        COALESCE(s.last_name, ''),
                        CASE WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> '' THEN ' ' || s.extension ELSE '' END
                    ) AS student_name,
                    a.uid,
                    sec.section_name,
                    a.attendance_date,
                    a.time_in,
                    a.time_out,
                    a.status,
                    a.created_at
                FROM attendance a
                INNER JOIN students s ON a.student_id = s.id
                LEFT JOIN sections sec ON s.section_id = sec.id
                WHERE s.section_id = %s
                ORDER BY a.attendance_date DESC, a.created_at DESC
            """, (selected_section,))
        else:
            cur.execute("""
                SELECT
                    a.id,
                    CONCAT(
                        COALESCE(s.first_name, ''),
                        CASE WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> '' THEN ' ' || s.middle_name ELSE '' END,
                        ' ',
                        COALESCE(s.last_name, ''),
                        CASE WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> '' THEN ' ' || s.extension ELSE '' END
                    ) AS student_name,
                    a.uid,
                    sec.section_name,
                    a.attendance_date,
                    a.time_in,
                    a.time_out,
                    a.status,
                    a.created_at
                FROM attendance a
                INNER JOIN students s ON a.student_id = s.id
                LEFT JOIN sections sec ON s.section_id = sec.id
                ORDER BY a.attendance_date DESC, a.created_at DESC
            """)

        attendance_rows = cur.fetchall()

    except Exception as e:
        print("Attendance page error:", e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template(
        "superadmin/attendance.html",
        sections=sections,
        selected_section=selected_section,
        attendance_rows=attendance_rows
    )


@sadmin.route('/backup-database')
def backup_database():
    return "Backup Database"


@sadmin.route('/logout')
def logout():
    return redirect(url_for('sadmin.login'))