from flask import Blueprint, render_template, request, session, redirect, url_for, flash, send_file
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import io
import csv
from datetime import date, datetime, timedelta

load_dotenv()

report_bp = Blueprint("report_bp", __name__, template_folder="templates")


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
# DB CONNECTION
# ─────────────────────────────────────────────

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise Exception("DATABASE_URL not found in .env")
    return psycopg2.connect(database_url.strip(), sslmode="require")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _parse_date(value: str, fallback):
    """Parse a YYYY-MM-DD string; return fallback on failure."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except Exception:
        return fallback


def _fetch_summary(cur, date_from, date_to):
    """Overall KPIs for the selected date range."""
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM students)                                AS total_students,
            (SELECT COUNT(*) FROM teachers)                                AS total_teachers,
            (SELECT COUNT(*) FROM sections)                                AS total_sections,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'present' THEN 1 ELSE 0 END), 0) AS total_present,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'late'    THEN 1 ELSE 0 END), 0) AS total_late,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'absent'  THEN 1 ELSE 0 END), 0) AS total_absent,
            COUNT(a.id)                                                    AS total_records
        FROM attendance a
        WHERE a.attendance_date BETWEEN %s AND %s
    """, (date_from, date_to))
    return cur.fetchone()


def _fetch_daily_trend(cur, date_from, date_to):
    """Daily present + late + absent counts for a trend chart."""
    cur.execute("""
        SELECT
            a.attendance_date                                                         AS day,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'present' THEN 1 ELSE 0 END),0) AS present,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'late'    THEN 1 ELSE 0 END),0) AS late,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'absent'  THEN 1 ELSE 0 END),0) AS absent
        FROM attendance a
        WHERE a.attendance_date BETWEEN %s AND %s
        GROUP BY a.attendance_date
        ORDER BY a.attendance_date ASC
    """, (date_from, date_to))
    return cur.fetchall()


def _fetch_section_summary(cur, date_from, date_to):
    """Per-section attendance breakdown."""
    cur.execute("""
        SELECT
            sec.section_name,
            sec.year_level,
            CONCAT_WS(' ', t.first_name, t.middle_name, t.last_name) AS adviser,
            COUNT(DISTINCT s.id)                                        AS total_students,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'present' THEN 1 ELSE 0 END),0) AS present,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'late'    THEN 1 ELSE 0 END),0) AS late,
            COALESCE(SUM(CASE WHEN LOWER(a.status) = 'absent'  THEN 1 ELSE 0 END),0) AS absent
        FROM sections sec
        LEFT JOIN teachers  t  ON sec.teacher_id = t.id
        LEFT JOIN students  s  ON s.section_id   = sec.id
        LEFT JOIN attendance a ON a.student_id   = s.id
                               AND a.attendance_date BETWEEN %s AND %s
        GROUP BY sec.id, sec.section_name, sec.year_level,
                 t.first_name, t.middle_name, t.last_name
        ORDER BY sec.year_level ASC, sec.section_name ASC
    """, (date_from, date_to))
    return cur.fetchall()


def _fetch_student_attendance(cur, date_from, date_to, section_id=None, status_filter=None, search=None):
    """Full per-student attendance list with optional filters."""
    query = """
        SELECT
            s.id                                                           AS student_id,
            CONCAT(
                COALESCE(s.last_name, ''), ', ',
                COALESCE(s.first_name, ''),
                CASE WHEN s.middle_name IS NOT NULL AND TRIM(s.middle_name) <> ''
                     THEN ' ' || s.middle_name ELSE '' END,
                CASE WHEN s.extension IS NOT NULL AND TRIM(s.extension) <> ''
                     THEN ' ' || s.extension ELSE '' END
            )                                                              AS student_name,
            sec.section_name,
            sec.year_level,
            COALESCE(SUM(CASE WHEN LOWER(a.status)='present' THEN 1 ELSE 0 END),0) AS present,
            COALESCE(SUM(CASE WHEN LOWER(a.status)='late'    THEN 1 ELSE 0 END),0) AS late,
            COALESCE(SUM(CASE WHEN LOWER(a.status)='absent'  THEN 1 ELSE 0 END),0) AS absent,
            COUNT(a.id)                                                    AS total_days,
            CASE
                WHEN COUNT(a.id) = 0 THEN 0
                ELSE ROUND(
                    (SUM(CASE WHEN LOWER(a.status) IN ('present','late') THEN 1 ELSE 0 END)::numeric
                     / COUNT(a.id)) * 100, 1
                )
            END                                                            AS attendance_rate
        FROM students s
        LEFT JOIN sections   sec ON s.section_id   = sec.id
        LEFT JOIN attendance a   ON a.student_id   = s.id
                                 AND a.attendance_date BETWEEN %s AND %s
        WHERE 1=1
    """
    params = [date_from, date_to]

    if section_id:
        query += " AND s.section_id = %s"
        params.append(section_id)

    if search:
        query += """
            AND (
                LOWER(s.last_name)  LIKE LOWER(%s)
                OR LOWER(s.first_name) LIKE LOWER(%s)
                OR LOWER(sec.section_name) LIKE LOWER(%s)
            )
        """
        like = f"%{search}%"
        params.extend([like, like, like])

    query += """
        GROUP BY s.id, s.last_name, s.first_name, s.middle_name, s.extension,
                 sec.section_name, sec.year_level
    """

    if status_filter == "perfect":
        query += " HAVING SUM(CASE WHEN LOWER(a.status)='absent' THEN 1 ELSE 0 END) = 0 AND COUNT(a.id) > 0"
    elif status_filter == "at_risk":
        query += """
            HAVING
                COUNT(a.id) > 0
                AND ROUND(
                    (SUM(CASE WHEN LOWER(a.status) IN ('present','late') THEN 1 ELSE 0 END)::numeric
                     / COUNT(a.id)) * 100, 1
                ) < 75
        """

    query += " ORDER BY sec.year_level ASC, sec.section_name ASC, s.last_name ASC, s.first_name ASC"

    cur.execute(query, params)
    return cur.fetchall()


def _fetch_rfid_stats(cur):
    """RFID card linkage statistics."""
    cur.execute("""
        SELECT
            COUNT(*)                                                    AS total_cards,
            SUM(CASE WHEN s.uid IS NOT NULL THEN 1 ELSE 0 END)         AS linked,
            SUM(CASE WHEN s.uid IS     NULL THEN 1 ELSE 0 END)         AS unlinked
        FROM rfid_cards r
        LEFT JOIN students s ON r.uid = s.uid
    """)
    return cur.fetchone()


def _fetch_recent_attendance(cur, limit=20):
    """Latest attendance logs."""
    cur.execute("""
        SELECT
            a.attendance_date,
            TO_CHAR(a.time_in,  'HH12:MI AM') AS time_in,
            TO_CHAR(a.time_out, 'HH12:MI AM') AS time_out,
            a.status,
            CONCAT(
                COALESCE(s.last_name, ''), ', ',
                COALESCE(s.first_name, '')
            )                               AS student_name,
            sec.section_name
        FROM attendance a
        JOIN students  s   ON a.student_id   = s.id
        LEFT JOIN sections sec ON s.section_id = sec.id
        ORDER BY a.attendance_date DESC, a.created_at DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


# ─────────────────────────────────────────────
# MAIN REPORT ROUTE
# ─────────────────────────────────────────────

@report_bp.route("/reports", methods=["GET"])
@login_required
def reports():
    today      = date.today()
    date_from  = _parse_date(request.args.get("date_from", ""), today - timedelta(days=30))
    date_to    = _parse_date(request.args.get("date_to",   ""), today)
    section_id = request.args.get("section_id", "").strip() or None
    status_filter = request.args.get("status_filter", "").strip() or None
    search     = request.args.get("search", "").strip() or None

    # Clamp range to max 1 year
    if (date_to - date_from).days > 365:
        date_from = date_to - timedelta(days=365)

    summary          = {}
    daily_trend      = []
    section_summary  = []
    student_list     = []
    rfid_stats       = {}
    recent_logs      = []
    sections         = []

    conn = None
    cur  = None

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        summary         = _fetch_summary(cur, date_from, date_to)
        daily_trend     = _fetch_daily_trend(cur, date_from, date_to)
        section_summary = _fetch_section_summary(cur, date_from, date_to)
        student_list    = _fetch_student_attendance(cur, date_from, date_to,
                                                    section_id, status_filter, search)
        rfid_stats      = _fetch_rfid_stats(cur)
        recent_logs     = _fetch_recent_attendance(cur)

        cur.execute("SELECT id, section_name, year_level FROM sections ORDER BY year_level, section_name")
        sections = cur.fetchall()

    except Exception as e:
        flash(f"Report error: {str(e)}", "error")

    finally:
        if cur:  cur.close()
        if conn: conn.close()

    # Compute overall attendance rate
    attended = (summary.get("total_present", 0) or 0) + (summary.get("total_late", 0) or 0)
    total    = (summary.get("total_records", 0) or 0)
    overall_rate = round((attended / total) * 100, 1) if total > 0 else 0

    # Serialize daily trend for chart.js
    trend_labels  = [str(r["day"]) for r in daily_trend]
    trend_present = [int(r["present"]) for r in daily_trend]
    trend_late    = [int(r["late"])    for r in daily_trend]
    trend_absent  = [int(r["absent"])  for r in daily_trend]

    return render_template(
        "superadmin/report.html",
        today=today,
        date_from=date_from,
        date_to=date_to,
        summary=summary,
        overall_rate=overall_rate,
        daily_trend=daily_trend,
        trend_labels=trend_labels,
        trend_present=trend_present,
        trend_late=trend_late,
        trend_absent=trend_absent,
        section_summary=section_summary,
        student_list=student_list,
        rfid_stats=rfid_stats,
        recent_logs=recent_logs,
        sections=sections,
        section_id=section_id,
        status_filter=status_filter,
        search=search,
    )


# ─────────────────────────────────────────────
# CSV EXPORT
# ─────────────────────────────────────────────

@report_bp.route("/reports/export/csv", methods=["GET"])
@login_required
def export_csv():
    today     = date.today()
    date_from = _parse_date(request.args.get("date_from", ""), today - timedelta(days=30))
    date_to   = _parse_date(request.args.get("date_to",   ""), today)
    section_id    = request.args.get("section_id", "").strip() or None
    status_filter = request.args.get("status_filter", "").strip() or None
    search        = request.args.get("search", "").strip() or None

    conn = None
    cur  = None

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        rows = _fetch_student_attendance(cur, date_from, date_to, section_id, status_filter, search)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Student Name", "Section", "Year Level",
                         "Present", "Late", "Absent", "Total Days", "Attendance Rate (%)"])
        for r in rows:
            writer.writerow([
                r["student_name"], r["section_name"] or "—", r["year_level"] or "—",
                r["present"], r["late"], r["absent"],
                r["total_days"], r["attendance_rate"],
            ])

        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            as_attachment=True,
            download_name=f"attendance_report_{timestamp}.csv",
            mimetype="text/csv",
        )

    except Exception as e:
        flash(f"Export error: {str(e)}", "error")
        return redirect(url_for("report_bp.reports"))

    finally:
        if cur:  cur.close()
        if conn: conn.close()


# ─────────────────────────────────────────────
# ATTENDANCE LOG EXPORT (per-record, not per-student)
# ─────────────────────────────────────────────

@report_bp.route("/reports/export/attendance-log", methods=["GET"])
@login_required
def export_attendance_log():
    today     = date.today()
    date_from = _parse_date(request.args.get("date_from", ""), today - timedelta(days=30))
    date_to   = _parse_date(request.args.get("date_to",   ""), today)

    conn = None
    cur  = None

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                a.attendance_date,
                TO_CHAR(a.time_in,  'HH12:MI AM') AS time_in,
                TO_CHAR(a.time_out, 'HH12:MI AM') AS time_out,
                a.status,
                CONCAT(s.last_name, ', ', s.first_name) AS student_name,
                sec.section_name,
                sec.year_level
            FROM attendance a
            JOIN students  s   ON a.student_id   = s.id
            LEFT JOIN sections sec ON s.section_id = sec.id
            WHERE a.attendance_date BETWEEN %s AND %s
            ORDER BY a.attendance_date DESC, s.last_name ASC
        """, (date_from, date_to))
        rows = cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "Student Name", "Section", "Year Level",
                         "Time In", "Time Out", "Status"])
        for r in rows:
            writer.writerow([
                r["attendance_date"], r["student_name"],
                r["section_name"] or "—", r["year_level"] or "—",
                r["time_in"] or "—", r["time_out"] or "—",
                r["status"],
            ])

        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            as_attachment=True,
            download_name=f"attendance_log_{timestamp}.csv",
            mimetype="text/csv",
        )

    except Exception as e:
        flash(f"Export error: {str(e)}", "error")
        return redirect(url_for("report_bp.reports"))

    finally:
        if cur:  cur.close()
        if conn: conn.close()