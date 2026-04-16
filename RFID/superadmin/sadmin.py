from flask import Blueprint, render_template
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
        port=os.getenv("DB_PORT", 5432)
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

        # If your students table has NO section_id column yet,
        # keep this as 0 for now
        students_without_section = 0
        sections_without_teacher = 0
        total_teachers = 0

        # Recent activity
        # Since you don't yet have attendance table,
        # use the latest registered students with linked RFID as placeholder
        cur.execute("""
            SELECT
                CONCAT(s.first_name, ' ', s.last_name) AS student_name,
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

        # Empty for now because teachers table is not yet available in your screenshot
        teachers_overview = []

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