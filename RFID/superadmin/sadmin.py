from flask import Blueprint, render_template, request, redirect, url_for, send_file, flash, session
from functools import wraps
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
import secrets
import requests as http_requests
from datetime import datetime, timedelta, date
import bcrypt

load_dotenv()

sadmin = Blueprint("sadmin", __name__, template_folder="template")


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
        raise Exception("❌ DATABASE_URL not found in .env")
    return psycopg2.connect(database_url.strip(), sslmode="require")


def get_pg_dump_path():
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


# ─────────────────────────────────────────────
# NEW: SAFE RFID REGISTRATION (prevents duplicate UIDs)
# ─────────────────────────────────────────────
def register_rfid_uid(uid_value: str):
    """
    Use this function EVERYWHERE you receive a new RFID tap/scan.
    It automatically prevents duplicate UIDs even if the same card is tapped again.
    """
    if not uid_value:
        return False
    uid_value = str(uid_value).strip().upper()

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # ON CONFLICT DO NOTHING → no duplicate rows will be created
        cur.execute("""
            INSERT INTO rfid_cards (uid, created_at)
            VALUES (%s, NOW())
            ON CONFLICT (uid) DO NOTHING
        """, (uid_value,))
        conn.commit()

        if cur.rowcount > 0:
            print(f"✅ New UID registered: {uid_value}")
            return True
        else:
            print(f"ℹ️ UID already exists (duplicate prevented): {uid_value}")
            return False
    except Exception as e:
        print(f"register_rfid_uid error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ─────────────────────────────────────────────
# PASSWORD HELPERS
# ─────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ─────────────────────────────────────────────
# SUPERADMIN ACCOUNT HELPERS
# ─────────────────────────────────────────────

def get_superadmin_account(username_or_email: str):
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM superadmin_accounts WHERE email = %s LIMIT 1",
            (username_or_email,)
        )
        return cur.fetchone()
    except Exception as e:
        print("get_superadmin_account error:", e)
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def update_superadmin_password(account_id: int, new_password: str) -> bool:
    conn = None
    cur  = None
    try:
        hashed = hash_password(new_password)
        conn   = get_db_connection()
        cur    = conn.cursor()
        cur.execute(
            "UPDATE superadmin_accounts SET password = %s WHERE id = %s",
            (hashed, account_id)
        )
        conn.commit()
        print(f"✅ Password updated in DB for superadmin id={account_id}")
        return True
    except Exception as e:
        print("update_superadmin_password error:", e)
        if conn:
            conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ─────────────────────────────────────────────
# EMAIL HELPER
# ─────────────────────────────────────────────

RESET_EMAIL_RECIPIENT = "bergoniaraymund@gmail.com"
_reset_tokens: dict = {}


def _send_reset_email(reset_link: str) -> bool:
    api_key   = os.getenv("RESEND_API_KEY", "")
    from_addr = os.getenv("RESEND_FROM", "DMRMINHS Portal <onboarding@resend.dev>")

    if not api_key:
        print("❌ RESEND_API_KEY not set in .env")
        return False

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;font-family:Inter,Arial,sans-serif;background:#f1f5f9;">
      <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
        <tr>
          <td align="center">
            <table width="520" cellpadding="0" cellspacing="0"
                   style="background:#ffffff;border-radius:16px;overflow:hidden;
                          box-shadow:0 4px 24px rgba(0,0,0,0.08);">
              <tr>
                <td style="background:linear-gradient(135deg,#1d4ed8,#4338ca);
                           padding:32px 40px;text-align:center;">
                  <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;">DMRMINHS</h1>
                  <p style="margin:4px 0 0;color:rgba(255,255,255,0.75);font-size:13px;">
                    Don Manuel Rivera Memorial Integrated NHS
                  </p>
                </td>
              </tr>
              <tr>
                <td style="padding:36px 40px;">
                  <p style="margin:0 0 16px;font-size:15px;color:#374151;font-weight:600;">
                    Hello, Superadmin 👋
                  </p>
                  <p style="margin:0 0 24px;font-size:14px;color:#6b7280;line-height:1.6;">
                    We received a request to reset the password for your superadmin account.
                    Click the button below to set a new password. This link is valid for
                    <strong style="color:#374151;">30 minutes</strong>.
                  </p>
                  <table cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                      <td align="center" style="padding:8px 0 28px;">
                        <a href="{reset_link}"
                           style="display:inline-block;background:linear-gradient(135deg,#1d4ed8,#4338ca);
                                  color:#ffffff;text-decoration:none;font-size:14px;font-weight:700;
                                  padding:14px 36px;border-radius:10px;">
                          Reset My Password
                        </a>
                      </td>
                    </tr>
                  </table>
                  <p style="margin:0 0 8px;font-size:12px;color:#9ca3af;">
                    Or copy and paste this link into your browser:
                  </p>
                  <p style="margin:0 0 24px;font-size:12px;color:#3b82f6;word-break:break-all;">
                    {reset_link}
                  </p>
                  <hr style="border:none;border-top:1px solid #f3f4f6;margin:0 0 20px;">
                  <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
                    If you did not request a password reset, you can safely ignore this email.
                  </p>
                </td>
              </tr>
              <tr>
                <td style="background:#f9fafb;padding:20px 40px;text-align:center;
                           border-top:1px solid #f3f4f6;">
                  <p style="margin:0;font-size:11px;color:#9ca3af;">
                    © {datetime.now().year} DMRMINHS · Pila, Laguna, Philippines
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    plain_body = (
        f"DMRMINHS Superadmin – Password Reset\n\n"
        f"Click the link below to reset your password (valid 30 minutes):\n"
        f"{reset_link}\n\n"
        f"If you did not request this, ignore this email."
    )

    payload = {
        "from":    from_addr,
        "to":      [RESET_EMAIL_RECIPIENT],
        "subject": "DMRMINHS Superadmin – Password Reset Request",
        "html":    html_body,
        "text":    plain_body,
    }

    try:
        response = http_requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=10,
        )
        if response.status_code in (200, 201):
            print(f"✅ Reset email sent via Resend → {RESET_EMAIL_RECIPIENT}")
            return True
        else:
            print(f"❌ Resend error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print("❌ Resend request failed:", e)
        return False


# ─────────────────────────────────────────────
# LOGIN / LOGOUT
# ─────────────────────────────────────────────

@sadmin.route('/Login', methods=['GET', 'POST'])
def login():
    if session.get("sadmin_logged_in"):
        return redirect(url_for("sadmin.index"))

    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        account = get_superadmin_account(username)

        if account is None:
            admin_username = os.getenv("SADMIN_USERNAME", "superadmin")
            admin_password = os.getenv("SADMIN_PASSWORD", "admin123")

            if username == admin_username and password == admin_password:
                session["sadmin_logged_in"] = True
                session["sadmin_username"]  = username
                flash("Welcome back, Superadmin!", "success")
                return redirect(url_for("sadmin.index"))
            else:
                flash("Invalid username or password.", "danger")
                return redirect(url_for("sadmin.login"))

        stored_hash = account.get("password", "")

        if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
            password_ok = check_password(password, stored_hash)
        else:
            password_ok = (password == stored_hash)
            if password_ok:
                update_superadmin_password(account["id"], password)

        if password_ok:
            session["sadmin_logged_in"]  = True
            session["sadmin_username"]   = username
            session["sadmin_account_id"] = account["id"]
            flash("Welcome back, Superadmin!", "success")
            return redirect(url_for("sadmin.index"))
        else:
            flash("Invalid username or password.", "danger")
            return redirect(url_for("sadmin.login"))

    return render_template("superadmin/login.html")


@sadmin.route('/logout')
def logout():
    session.pop("sadmin_logged_in",  None)
    session.pop("sadmin_username",   None)
    session.pop("sadmin_account_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for('sadmin.login'))


# ─────────────────────────────────────────────
# FORGOT PASSWORD – SEND EMAIL
# ─────────────────────────────────────────────

@sadmin.route('/forgot-password/send', methods=['POST'])
def forgot_password_send():
    account = get_superadmin_account(RESET_EMAIL_RECIPIENT)
    account_id = account["id"] if account else None

    now     = datetime.utcnow()
    expired = [t for t, (exp, _) in _reset_tokens.items() if exp < now]
    for t in expired:
        _reset_tokens.pop(t, None)

    session.pop("reset_done", None)

    token  = secrets.token_urlsafe(48)
    expiry = now + timedelta(minutes=30)
    _reset_tokens[token] = (expiry, account_id)

    reset_link = url_for("sadmin.reset_password", token=token, _external=True)
    ok = _send_reset_email(reset_link)

    if ok:
        flash(
            f"✅ A reset link has been sent to {RESET_EMAIL_RECIPIENT}. "
            "Please check your inbox (and spam folder).",
            "success"
        )
    else:
        flash("⚠️ Could not send the email. Check RESEND_API_KEY in .env.", "warning")

    return redirect(url_for("sadmin.login"))


# ─────────────────────────────────────────────
# FORGOT PASSWORD – RESET PAGE
# ─────────────────────────────────────────────

@sadmin.route('/forgot-password/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    now        = datetime.utcnow()
    token_data = _reset_tokens.get(token)
    valid_token = (token_data is not None and token_data[0] > now)

    if request.method == 'GET':
        if session.get("reset_done"):
            flash("Password has already been reset. Please log in.", "info")
            return redirect(url_for("sadmin.login"))
        return render_template(
            "superadmin/forgot_password.html",
            token=token,
            valid_token=valid_token
        )

    if not valid_token:
        flash("This reset link has expired or is invalid.", "danger")
        return redirect(url_for("sadmin.login"))

    new_password     = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    errors = []
    if len(new_password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not any(c.isupper() for c in new_password):
        errors.append("Password must contain at least one uppercase letter.")
    if not any(c.isdigit() for c in new_password):
        errors.append("Password must contain at least one number.")
    if new_password != confirm_password:
        errors.append("Passwords do not match.")

    if errors:
        for err in errors:
            flash(err, "danger")
        return render_template(
            "superadmin/forgot_password.html",
            token=token,
            valid_token=True
        )

    _, account_id = token_data

    if account_id is not None:
        success = update_superadmin_password(account_id, new_password)
    else:
        account = get_superadmin_account(RESET_EMAIL_RECIPIENT)
        if account:
            success = update_superadmin_password(account["id"], new_password)
        else:
            success = False

    if not success:
        flash("⚠️ Could not update the password. Please try again.", "danger")
        return render_template(
            "superadmin/forgot_password.html",
            token=token,
            valid_token=True
        )

    _update_env_password(new_password)
    _reset_tokens.pop(token, None)

    session["reset_done"]    = True
    session["reset_done_at"] = datetime.utcnow().isoformat()
    session.pop("sadmin_logged_in",  None)
    session.pop("sadmin_username",   None)
    session.pop("sadmin_account_id", None)

    flash("✅ Password updated successfully! Please log in with your new password.", "success")
    return redirect(url_for("sadmin.login"))


def _update_env_password(new_password: str):
    os.environ["SADMIN_PASSWORD"] = new_password

    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".env")
    )

    if not os.path.exists(env_path):
        print(f"⚠️ .env not found at {env_path} — password updated in memory only.")
        return

    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated   = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("SADMIN_PASSWORD"):
            new_lines.append(f'SADMIN_PASSWORD={new_password}\n')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f'\nSADMIN_PASSWORD={new_password}\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("✅ SADMIN_PASSWORD updated in .env")


# ─────────────────────────────────────────────
# DASHBOARD (protected)
# ─────────────────────────────────────────────

@sadmin.route('/')
@login_required
def index():
    today = date.today()

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
    cur  = None

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT COUNT(*) AS count FROM teachers")
        total_teachers = cur.fetchone()["count"] or 0

        cur.execute("SELECT COUNT(*) AS count FROM students")
        total_students = cur.fetchone()["count"] or 0

        cur.execute("SELECT COUNT(*) AS count FROM rfid_cards")
        total_rfid_cards = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM rfid_cards r
            LEFT JOIN students s ON r.uid = s.uid
            WHERE s.uid IS NULL
        """)
        unlinked_rfid = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM students
            WHERE section_id IS NULL
        """)
        students_without_section = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM sections
            WHERE teacher_id IS NULL
        """)
        sections_without_teacher = cur.fetchone()["count"] or 0

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM teachers t
            LEFT JOIN sections s ON s.teacher_id = t.id
            WHERE s.id IS NULL
        """)
        teachers_without_section = cur.fetchone()["count"] or 0

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
                "uid":          row["uid"],
                "time":         row["time"],
                "status":       row["status"]
            }
            for row in rows
        ]

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
                "id":             row["id"],
                "full_name":      row["full_name"],
                "email":          row["email"],
                "contact_number": row["contact_number"],
                "created_at":     row["created_at"],
                "total_sections": row["total_sections"]
            }
            for row in teacher_rows
        ]

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
        today=today,
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


# ─────────────────────────────────────────────
# UPDATED UID ROUTE (no duplicates + shows linked student)
# ─────────────────────────────────────────────

@sadmin.route('/UID')
@login_required
def uid():
    conn = None
    cur  = None
    uids = []

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT DISTINCT ON (r.uid)
                r.id,
                r.uid,
                r.created_at,
                CASE 
                    WHEN s.id IS NOT NULL THEN 'Used' 
                    ELSE 'Available' 
                END AS status,
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
                ) AS student_name
            FROM rfid_cards r
            LEFT JOIN students s ON r.uid = s.uid
            ORDER BY r.uid, r.created_at DESC
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


# ─────────────────────────────────────────────
# OTHER PROTECTED ROUTES
# ─────────────────────────────────────────────

@sadmin.route('/attendance')
@login_required
def attendance():
    selected_section = request.args.get("section", "").strip()

    conn            = None
    cur             = None
    sections        = []
    attendance_rows = []

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, section_name
            FROM sections
            ORDER BY section_name ASC
        """)
        sections = cur.fetchall()

        query = """
            SELECT
                s.id AS student_id,
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
                sec.section_name,
                latest_attendance.attendance_date,
                latest_attendance.time_in,
                latest_attendance.time_out,
                latest_attendance.status,
                latest_attendance.created_at
            FROM students s
            LEFT JOIN sections sec ON s.section_id = sec.id
            LEFT JOIN LATERAL (
                SELECT
                    a.attendance_date,
                    a.time_in,
                    a.time_out,
                    a.status,
                    a.created_at
                FROM attendance a
                WHERE a.student_id = s.id
                ORDER BY a.attendance_date DESC, a.created_at DESC
                LIMIT 1
            ) latest_attendance ON TRUE
        """

        params = []
        if selected_section:
            query += " WHERE s.section_id = %s "
            params.append(selected_section)

        query += """
            ORDER BY
                sec.section_name ASC,
                s.last_name ASC,
                s.first_name ASC
        """

        cur.execute(query, params)
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
@login_required
def backup_database():
    return render_template("superadmin/backup_database.html")


@sadmin.route('/backup-database/download/<backup_type>')
@login_required
def download_backup(backup_type):
    backup_type = backup_type.lower().strip()
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")

    if backup_type == "sql":
        pg_dump_path = get_pg_dump_path()

        if not pg_dump_path:
            flash("pg_dump not found. Lagay ka ng PG_DUMP_PATH sa .env or install PostgreSQL tools.", "error")
            return redirect(url_for("sadmin.backup_database"))

        db_host     = os.getenv("DB_HOST")
        db_name     = os.getenv("DB_NAME")
        db_user     = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        db_port     = str(os.getenv("DB_PORT", 5432))

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".sql")
        temp_file.close()

        env               = os.environ.copy()
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
            subprocess.run(command, env=env, capture_output=True, text=True, check=True)
            return send_file(
                temp_file.name,
                as_attachment=True,
                download_name=f"database_backup_{timestamp}.sql"
            )
        except subprocess.CalledProcessError as e:
            print("SQL backup error:", e.stderr)
            flash(f"SQL backup failed: {e.stderr}", "error")
            return redirect(url_for("sadmin.backup_database"))

    elif backup_type == "csv":
        conn = None
        cur  = None

        try:
            conn        = get_db_connection()
            cur         = conn.cursor(cursor_factory=RealDictCursor)
            table_names = get_table_names(cur)
            memory_file = io.BytesIO()

            with zipfile.ZipFile(memory_file, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for table in table_names:
                    cur.execute(f'SELECT * FROM "{table}"')
                    rows = cur.fetchall()

                    csv_buffer = io.StringIO()
                    writer     = csv.writer(csv_buffer)

                    if rows:
                        headers = list(rows[0].keys())
                        writer.writerow(headers)
                        for row in rows:
                            writer.writerow([row.get(h, "") for h in headers])
                    else:
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
        cur  = None

        try:
            conn        = get_db_connection()
            cur         = conn.cursor(cursor_factory=RealDictCursor)
            table_names = get_table_names(cur)
            all_data    = {}

            for table in table_names:
                cur.execute(f'SELECT * FROM "{table}"')
                all_data[table] = cur.fetchall()

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