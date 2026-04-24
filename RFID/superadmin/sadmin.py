from flask import Blueprint, render_template, request, redirect, url_for, send_file, flash
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import io
import csv
import json
import zipfile
import tempfile
import subprocess
import shutil
from datetime import datetime

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


def get_pg_dump_path():
    """
    Hanapin ang pg_dump.
    Priority:
    1. PG_DUMP_PATH sa .env
    2. system PATH
    3. common Windows install paths
    """
    env_pg_dump = os.getenv("PG_DUMP_PATH")
    if env_pg_dump and os.path.exists(env_pg_dump):
        return env_pg_dump

    path_pg_dump = shutil.which("pg_dump")
    if path_pg_dump:
        return path_pg_dump

    possible_paths = [
        r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\13\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\12\bin\pg_dump.exe",
    ]

    for p in possible_paths:
        if os.path.exists(p):
            return p

    return None


def get_table_names(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [row["table_name"] for row in cur.fetchall()]


def safe_filename(name):
    return "".join(c if c.isalnum() or c in ("_", "-", ".") else "_" for c in name)


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
    teachers_without_section = 0

    recent_activities = []
    teachers_overview = []
    teachers_without_section_list = []

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Total teachers
        cur.execute("SELECT COUNT(*) AS count FROM teachers")
        total_teachers = cur.fetchone()["count"] or 0

        # Total students
        cur.execute("SELECT COUNT(*) AS count FROM students")
        total_students = cur.fetchone()["count"] or 0

        # Total RFID cards
        cur.execute("SELECT COUNT(*) AS count FROM rfid_cards")
        total_rfid_cards = cur.fetchone()["count"] or 0

        # Unlinked RFID cards
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM rfid_cards r
            LEFT JOIN students s ON r.uid = s.uid
            WHERE s.uid IS NULL
        """)
        unlinked_rfid = cur.fetchone()["count"] or 0

        # Students without section
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM students
            WHERE section_id IS NULL
        """)
        students_without_section = cur.fetchone()["count"] or 0

        # Sections without teacher
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM sections
            WHERE teacher_id IS NULL
        """)
        sections_without_teacher = cur.fetchone()["count"] or 0

        # Teachers without section
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM teachers t
            LEFT JOIN sections s ON s.teacher_id = t.id
            WHERE s.id IS NULL
        """)
        teachers_without_section = cur.fetchone()["count"] or 0

        # Teachers without section list
        cur.execute("""
            SELECT
                t.id,
                CONCAT(
                    COALESCE(t.first_name, ''),
                    CASE
                        WHEN t.middle_name IS NOT NULL AND TRIM(t.middle_name) <> ''
                        THEN ' ' || t.middle_name
                        ELSE ''
                    END,
                    ' ',
                    COALESCE(t.last_name, '')
                ) AS full_name,
                t.email,
                t.contact_number,
                TO_CHAR(t.created_at, 'YYYY-MM-DD HH12:MI AM') AS created_at
            FROM teachers t
            LEFT JOIN sections s ON s.teacher_id = t.id
            WHERE s.id IS NULL
            ORDER BY t.created_at DESC
        """)
        teachers_without_section_list = cur.fetchall()

        # Recent activity
        cur.execute("""
            SELECT
                CONCAT(
                    COALESCE(s.first_name, ''),
                    CASE
                        WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> ''
                        THEN ' ' || s.middle_name
                        ELSE ''
                    END,
                    ' ',
                    COALESCE(s.last_name, ''),
                    CASE
                        WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> ''
                        THEN ' ' || s.extension
                        ELSE ''
                    END
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

        # Teacher overview with total sections
        cur.execute("""
            SELECT
                t.id,
                CONCAT(
                    COALESCE(t.first_name, ''),
                    CASE
                        WHEN t.middle_name IS NOT NULL AND TRIM(t.middle_name) <> ''
                        THEN ' ' || t.middle_name
                        ELSE ''
                    END,
                    ' ',
                    COALESCE(t.last_name, '')
                ) AS full_name,
                t.email,
                t.contact_number,
                TO_CHAR(t.created_at, 'YYYY-MM-DD HH12:MI AM') AS created_at,
                COUNT(s.id) AS total_sections
            FROM teachers t
            LEFT JOIN sections s ON s.teacher_id = t.id
            GROUP BY t.id, t.first_name, t.middle_name, t.last_name, t.email, t.contact_number, t.created_at
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
                "created_at": row["created_at"],
                "total_sections": row["total_sections"]
            }
            for row in teacher_rows
        ]

        # Attendance today
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM attendance
            WHERE attendance_date = CURRENT_DATE AND LOWER(status) = 'present'
        """)
        present_today = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM attendance
            WHERE attendance_date = CURRENT_DATE AND LOWER(status) = 'late'
        """)
        late_today = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM students
            WHERE id NOT IN (
                SELECT DISTINCT student_id
                FROM attendance
                WHERE attendance_date = CURRENT_DATE
            )
        """)
        absent_today = cur.fetchone()["count"] or 0

        total_attendance_base = present_today + late_today + absent_today
        if total_attendance_base > 0:
            attendance_rate = round(((present_today + late_today) / total_attendance_base) * 100, 2)
        else:
            attendance_rate = 0

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
        teachers_without_section=teachers_without_section,
        recent_activities=recent_activities,
        teachers_overview=teachers_overview,
        teachers_without_section_list=teachers_without_section_list
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

        cur.execute("""
            SELECT id, section_name
            FROM sections
            ORDER BY section_name ASC
        """)
        sections = cur.fetchall()

        if selected_section:
            cur.execute("""
                SELECT
                    a.id,
                    CONCAT(
                        COALESCE(s.first_name, ''),
                        CASE
                            WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> ''
                            THEN ' ' || s.middle_name
                            ELSE ''
                        END,
                        ' ',
                        COALESCE(s.last_name, ''),
                        CASE
                            WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> ''
                            THEN ' ' || s.extension
                            ELSE ''
                        END
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
                        CASE
                            WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> ''
                            THEN ' ' || s.middle_name
                            ELSE ''
                        END,
                        ' ',
                        COALESCE(s.last_name, ''),
                        CASE
                            WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> ''
                            THEN ' ' || s.extension
                            ELSE ''
                        END
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
    return render_template("superadmin/backup_database.html")


@sadmin.route('/backup-database/download/<backup_type>')
def download_backup(backup_type):
    backup_type = backup_type.lower().strip()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if backup_type == "sql":
        pg_dump_path = get_pg_dump_path()

        if not pg_dump_path:
            flash("pg_dump not found. Lagay ka ng PG_DUMP_PATH sa .env or install PostgreSQL tools.", "error")
            return redirect(url_for("sadmin.backup_database"))

        db_host = os.getenv("DB_HOST")
        db_name = os.getenv("DB_NAME")
        db_user = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        db_port = str(os.getenv("DB_PORT", 5432))

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".sql")
        temp_file.close()

        env = os.environ.copy()
        env["PGPASSWORD"] = db_password

        command = [
            pg_dump_path,
            "-h", db_host,
            "-p", db_port,
            "-U", db_user,
            "-d", db_name,
            "-F", "p",
            "-f", temp_file.name
        ]

        try:
            result = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                check=True
            )

            download_name = f"database_backup_{timestamp}.sql"
            return send_file(
                temp_file.name,
                as_attachment=True,
                download_name=download_name
            )

        except subprocess.CalledProcessError as e:
            print("SQL backup error:", e.stderr)
            flash(f"SQL backup failed: {e.stderr}", "error")
            return redirect(url_for("sadmin.backup_database"))

    elif backup_type == "csv":
        conn = None
        cur = None

        try:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            table_names = get_table_names(cur)

            memory_file = io.BytesIO()

            with zipfile.ZipFile(memory_file, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for table in table_names:
                    cur.execute(f'SELECT * FROM "{table}"')
                    rows = cur.fetchall()

                    csv_buffer = io.StringIO()
                    writer = csv.writer(csv_buffer)

                    if rows:
                        headers = list(rows[0].keys())
                        writer.writerow(headers)
                        for row in rows:
                            writer.writerow([row.get(h, "") for h in headers])
                    else:
                        # kahit empty table, lagyan pa rin header based on table structure
                        cur.execute("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = %s
                            ORDER BY ordinal_position
                        """, (table,))
                        headers = [col["column_name"] for col in cur.fetchall()]
                        if headers:
                            writer.writerow(headers)

                    zf.writestr(f"{safe_filename(table)}.csv", csv_buffer.getvalue())

            memory_file.seek(0)
            return send_file(
                memory_file,
                as_attachment=True,
                download_name=f"database_csv_backup_{timestamp}.zip",
                mimetype="application/zip"
            )

        except Exception as e:
            print("CSV backup error:", e)
            flash(f"CSV backup failed: {str(e)}", "error")
            return redirect(url_for("sadmin.backup_database"))

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    elif backup_type == "json":
        conn = None
        cur = None

        try:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            table_names = get_table_names(cur)
            all_data = {}

            for table in table_names:
                cur.execute(f'SELECT * FROM "{table}"')
                rows = cur.fetchall()
                all_data[table] = rows

            json_bytes = io.BytesIO(
                json.dumps(all_data, default=str, indent=4).encode("utf-8")
            )

            return send_file(
                json_bytes,
                as_attachment=True,
                download_name=f"database_backup_{timestamp}.json",
                mimetype="application/json"
            )

        except Exception as e:
            print("JSON backup error:", e)
            flash(f"JSON backup failed: {str(e)}", "error")
            return redirect(url_for("sadmin.backup_database"))

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    else:
        flash("Invalid backup type selected.", "error")
        return redirect(url_for("sadmin.backup_database"))


@sadmin.route('/logout')
def logout():
    return redirect(url_for('sadmin.login'))