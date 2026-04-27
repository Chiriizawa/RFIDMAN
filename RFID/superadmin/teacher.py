from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.security import generate_password_hash

load_dotenv()

teacher_bp = Blueprint("teacher_bp", __name__, template_folder="template")


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
# CONSTANTS & VALIDATION (same style as student validation)
# ─────────────────────────────────────────────

VALID_EXTENSIONS = {'', 'Jr.', 'Sr.', 'II', 'III', 'IV', 'V'}

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
    # STRICT VALIDATION: Only domains in the ALLOWED_DOMAINS list are accepted
    # This makes 1234@ggmail.com INVALID
    return domain in ALLOWED_DOMAINS


def validate_teacher_fields(last_name, first_name, middle_name, extension,
                            contact_number, email):
    """
    Returns a list of error strings. Empty list = all valid.
    Same validation style as students.
    """
    errors = []

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
        errors.append("Extension must be one of: Jr., Sr., II, III, IV, V.")

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
    if not email:
        errors.append("Email address is required.")
    elif not re.match(r'^[^\s@]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        errors.append("Please enter a valid email address.")
    elif not is_valid_email_domain(email):
        domain_part = email.split('@')[-1] if '@' in email else ''
        errors.append(f'"{domain_part}" is not an accepted email domain. Use Gmail, Yahoo, Outlook, iCloud, or a valid school/government email.')

    return errors


# ─────────────────────────────────────────────
# DB CONNECTION
# ─────────────────────────────────────────────

def get_db_connection():
    from dotenv import load_dotenv
    import psycopg2

    load_dotenv()

    database_url = os.getenv("DATABASE_URL")

    print("🔥 USING DB URL:", database_url)

    if not database_url:
        raise Exception("❌ DATABASE_URL missing")

    return psycopg2.connect(
        database_url.strip(),
        sslmode="require"
    )


def send_teacher_create_password_email(to_email, first_name, last_name, reset_link):
    mail_host = os.getenv("MAIL_HOST")
    mail_port = int(os.getenv("MAIL_PORT", 587))
    mail_username = os.getenv("MAIL_USERNAME")
    mail_password = os.getenv("MAIL_PASSWORD")
    mail_from = os.getenv("MAIL_FROM", mail_username)

    if not mail_host or not mail_username or not mail_password:
        raise Exception("Mail configuration is missing in .env")

    subject = "Create Your Teacher Account Password"

    body = f"""
    <html>
    <body style="margin:0;padding:0;background:#f4f7fb;font-family:Arial,sans-serif;">
        <div style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,0.08);">
            <div style="background:linear-gradient(90deg,#2563eb,#1d4ed8);padding:24px 32px;">
                <h2 style="margin:0;color:#ffffff;">Teacher Account Created</h2>
            </div>

            <div style="padding:32px;">
                <p style="font-size:16px;color:#1f2937;margin-top:0;">
                    Hello <strong>{first_name} {last_name}</strong>,
                </p>

                <p style="font-size:15px;color:#4b5563;line-height:1.7;">
                    Your teacher account has been created successfully.
                    Click the button below to create your password.
                </p>

                <div style="margin:32px 0;text-align:center;">
                    <a href="{reset_link}"
                       style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:10px;font-weight:bold;font-size:15px;">
                       Create Password
                    </a>
                </div>

                <p style="font-size:14px;color:#6b7280;line-height:1.6;">
                    If the button does not work, copy and paste this link into your browser:
                </p>

                <p style="font-size:13px;color:#2563eb;word-break:break-all;">
                    {reset_link}
                </p>
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = mail_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP(mail_host, mail_port) as server:
        server.starttls()
        server.login(mail_username, mail_password)
        server.sendmail(mail_from, to_email, msg.as_string())


@teacher_bp.route("/teachers", methods=["GET"])
@login_required
def teachers():
    teachers_list = []
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id,
                   last_name,
                   first_name,
                   middle_name,
                   extension,
                   contact_number,
                   email,
                   created_at
            FROM teachers
            ORDER BY id DESC
        """)
        teachers_list = cur.fetchall()

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template("superadmin/teachers.html", teachers=teachers_list)


@teacher_bp.route("/teachers/<int:teacher_id>/students", methods=["GET"])
@login_required
def view_teacher_students(teacher_id):
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id,
                last_name,
                first_name,
                middle_name,
                extension
            FROM teachers
            WHERE id = %s
        """, (teacher_id,))
        teacher = cur.fetchone()

        if not teacher:
            return jsonify({
                "success": False,
                "message": "Teacher not found.",
                "teacher": None,
                "students": []
            }), 404

        cur.execute("""
            SELECT s.id,
                   s.last_name,
                   s.first_name,
                   s.middle_name,
                   s.extension,
                   sec.section_name,
                   sec.year_level
            FROM students s
            INNER JOIN sections sec ON sec.id = s.section_id
            WHERE sec.teacher_id = %s
            ORDER BY sec.year_level ASC,
                     sec.section_name ASC,
                     s.last_name ASC,
                     s.first_name ASC
        """, (teacher_id,))
        students = cur.fetchall()

        teacher_name = f"{teacher['last_name']}, {teacher['first_name']}"

        if teacher["middle_name"]:
            teacher_name += f" {teacher['middle_name']}"

        if teacher["extension"]:
            teacher_name += f" {teacher['extension']}"

        return jsonify({
            "success": True,
            "teacher": {
                "id": teacher["id"],
                "name": teacher_name
            },
            "students": students
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Database error: {str(e)}",
            "teacher": None,
            "students": []
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@teacher_bp.route("/teachers/add", methods=["POST"])
@login_required
def add_teacher():
    last_name = request.form.get("last_name", "").strip()
    first_name = request.form.get("first_name", "").strip()
    middle_name = request.form.get("middle_name", "").strip()
    extension = request.form.get("extension", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    email = request.form.get("email", "").strip()

    # New validation (same style as students)
    errors = validate_teacher_fields(
        last_name, first_name, middle_name, extension,
        contact_number, email
    )
    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(url_for("teacher_bp.teachers"))

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Duplicate email check
        cur.execute("SELECT id FROM teachers WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This email is already registered.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        # Duplicate contact number check
        cur.execute("SELECT id FROM teachers WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            cur.close()
            conn.close()
            flash("This contact number is already registered.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        # INSERT (teacher_id removed as it was not present in the form)
        cur.execute("""
            INSERT INTO teachers (
                last_name,
                first_name,
                middle_name,
                extension,
                contact_number,
                email
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            last_name,
            first_name,
            middle_name if middle_name else None,
            extension if extension else None,
            contact_number,
            email
        ))

        conn.commit()
        flash("Teacher added successfully.", "success")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error adding teacher: {str(e)}", "error")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for("teacher_bp.teachers"))


@teacher_bp.route("/create-password/<token>", methods=["GET", "POST"])
def create_password(token):
    # NOTE: No @login_required here — teachers need this without being logged in
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, email, reset_token
            FROM teacher_accounts
            WHERE reset_token = %s
        """, (token,))
        account = cur.fetchone()

        if not account:
            flash("Invalid or expired password link.", "error")
            return render_template("superadmin/create_password.html", token=token)

        if request.method == "POST":
            password = request.form.get("password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            if not password or not confirm_password:
                flash("Password and Confirm Password are required.", "error")
                return render_template("superadmin/create_password.html", token=token)

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("superadmin/create_password.html", token=token)

            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return render_template("superadmin/create_password.html", token=token)

            hashed_password = generate_password_hash(password)

            cur.execute("""
                UPDATE teacher_accounts
                SET password = %s,
                    reset_token = NULL
                WHERE id = %s
            """, (hashed_password, account["id"]))

            conn.commit()
            flash("Password created successfully. You can now log in.", "success")
            return redirect(url_for("teacher_bp.teachers"))

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error: {str(e)}", "error")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template("superadmin/create_password.html", token=token)


@teacher_bp.route("/teachers/test-email")
@login_required
def test_email():
    test_to = request.args.get("email", "").strip()

    if not test_to:
        flash("Please provide an email in the URL. Example: /teachers/test-email?email=your@email.com", "error")
        return redirect(url_for("teacher_bp.teachers"))

    sample_link = request.host_url.rstrip("/") + "/create-password/sample-token"

    try:
        send_teacher_create_password_email(
            to_email=test_to,
            first_name="Test",
            last_name="Teacher",
            reset_link=sample_link
        )
        flash(f"Test email sent successfully to {test_to}", "success")
    except Exception as e:
        flash(f"Failed to send test email: {str(e)}", "error")

    return redirect(url_for("teacher_bp.teachers"))