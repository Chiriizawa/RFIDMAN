from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
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
# CONSTANTS & VALIDATION
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
    return domain in ALLOWED_DOMAINS


def validate_teacher_fields(last_name, first_name, middle_name, extension,
                            contact_number, email):
    errors = []

    if not last_name:
        errors.append("Last name is required.")
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", last_name):
        errors.append("Last name must contain letters only.")
    elif len(last_name) < 3:
        errors.append("Last name must be at least 3 characters.")

    if not first_name:
        errors.append("First name is required.")
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", first_name):
        errors.append("First name must contain letters only.")
    elif len(first_name) < 3:
        errors.append("First name must be at least 3 characters.")

    if middle_name:
        if not re.match(r"^[a-zA-ZÀ-ÿ\s'\-]+$", middle_name):
            errors.append("Middle name must contain letters only.")
        elif len(middle_name) < 2:
            errors.append("Middle name must be at least 2 characters.")

    if extension and extension not in VALID_EXTENSIONS:
        errors.append("Extension must be one of: Jr., Sr., II, III, IV, V.")

    if not contact_number:
        errors.append("Contact number is required.")
    elif not contact_number.isdigit():
        errors.append("Contact number must contain digits only.")
    elif len(contact_number) != 11:
        errors.append(f"Contact number must be exactly 11 digits (got {len(contact_number)}).")
    elif not contact_number.startswith('09'):
        errors.append("Contact number must start with 09.")

    if not email:
        errors.append("Email address is required.")
    elif not re.match(r'^[^\s@]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        errors.append("Please enter a valid email address.")
    elif not is_valid_email_domain(email):
        domain_part = email.split('@')[-1] if '@' in email else ''
        errors.append(
            f'"{domain_part}" is not an accepted email domain. '
            f'Use Gmail, Yahoo, Outlook, iCloud, or a valid school/government email.'
        )

    return errors


# ─────────────────────────────────────────────
# DB CONNECTION
# ─────────────────────────────────────────────

def get_db_connection():
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    print("🔥 USING DB URL:", database_url)
    if not database_url:
        raise Exception("❌ DATABASE_URL missing")
    return psycopg2.connect(database_url.strip(), sslmode="require")


# ─────────────────────────────────────────────
# EMAIL FUNCTION - SENT WHEN TEACHER IS ADDED
# ─────────────────────────────────────────────

def send_teacher_invitation_email(to_email, first_name, last_name, reset_link):
    """
    Sends an email invitation to the teacher with a password setup link
    This is triggered automatically when a teacher is added
    """
    mail_host = os.getenv("MAIL_HOST")
    mail_port = int(os.getenv("MAIL_PORT", 587))
    mail_username = os.getenv("MAIL_USERNAME")
    mail_password = os.getenv("MAIL_PASSWORD")
    mail_from = os.getenv("MAIL_FROM", mail_username)

    if not mail_host or not mail_username or not mail_password:
        print("⚠️ Warning: Mail configuration is missing in .env")
        raise Exception("Mail configuration is missing. Please check your .env file.")

    subject = "🎓 Create Your Teacher Account Password"

    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Create Your Teacher Password</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background: #f4f7fb;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            }}
            .container {{
                max-width: 560px;
                margin: 40px auto;
                background: #ffffff;
                border-radius: 20px;
                overflow: hidden;
                box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1);
            }}
            .header {{
                background: linear-gradient(135deg, #2563eb, #1e40af);
                padding: 40px 32px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                color: #ffffff;
                font-size: 28px;
                font-weight: 700;
            }}
            .header p {{
                margin: 10px 0 0;
                color: #bfdbfe;
                font-size: 15px;
            }}
            .content {{
                padding: 40px 32px;
            }}
            .greeting {{
                font-size: 16px;
                color: #1f2937;
                margin-bottom: 20px;
                line-height: 1.6;
            }}
            .info-box {{
                background: #eff6ff;
                border-left: 4px solid #2563eb;
                padding: 16px 20px;
                margin: 24px 0;
                border-radius: 8px;
            }}
            .info-box p {{
                margin: 0;
                color: #1e40af;
                font-size: 14px;
            }}
            .button {{
                display: inline-block;
                background: linear-gradient(135deg, #2563eb, #1e40af);
                color: white;
                text-decoration: none;
                padding: 14px 32px;
                font-size: 16px;
                font-weight: 600;
                border-radius: 12px;
                margin: 24px 0;
                transition: transform 0.2s, box-shadow 0.2s;
            }}
            .button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 20px -5px rgba(37,99,235,0.3);
            }}
            .expiry-note {{
                background: #fef3c7;
                border: 1px solid #fde68a;
                border-radius: 12px;
                padding: 14px 18px;
                margin-top: 24px;
                font-size: 13px;
                color: #92400e;
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            .footer {{
                background: #f9fafb;
                border-top: 1px solid #e5e7eb;
                padding: 24px 32px;
                text-align: center;
            }}
            .footer p {{
                margin: 0;
                font-size: 12px;
                color: #9ca3af;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎓 Teacher Account Setup</h1>
                <p>Complete your registration</p>
            </div>
            
            <div class="content">
                <div class="greeting">
                    <strong>Hello {first_name} {last_name},</strong>
                </div>
                
                <p style="color: #4b5563; line-height: 1.6; margin-bottom: 24px;">
                    Your teacher account has been successfully created in our school management system. 
                    Please click the button below to set up your password and activate your account.
                </p>
                
                <div class="info-box">
                    <p>🔐 This link is valid for <strong>24 hours</strong> from the time this email was sent.</p>
                </div>
                
                <div style="text-align: center;">
                    <a href="{reset_link}" class="button">Create My Password →</a>
                </div>
                
                <p style="font-size: 13px; color: #6b7280; text-align: center; margin-top: 20px;">
                    Or copy and paste this link into your browser:<br>
                    <span style="color: #2563eb; word-break: break-all;">{reset_link}</span>
                </p>
                
                <div class="expiry-note">
                    <span style="font-size: 20px;">⏰</span>
                    <span><strong>Link expires in 24 hours.</strong> If expired, please contact your school administrator to send a new link.</span>
                </div>
            </div>
            
            <div class="footer">
                <p>This is an automated message from your school management system.</p>
                <p>If you didn't request this, please ignore this email.</p>
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

    try:
        with smtplib.SMTP(mail_host, mail_port) as server:
            server.starttls()
            server.login(mail_username, mail_password)
            server.sendmail(mail_from, to_email, msg.as_string())
        print(f"✅ Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {str(e)}")
        raise e


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@teacher_bp.route("/teachers", methods=["GET"])
@login_required
def teachers():
    teachers_list = []
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, last_name, first_name, middle_name,
                   extension, contact_number, email, created_at
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
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, last_name, first_name, middle_name, extension
            FROM teachers WHERE id = %s
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
            SELECT s.id, s.last_name, s.first_name, s.middle_name, s.extension,
                   sec.section_name, sec.year_level
            FROM students s
            INNER JOIN sections sec ON sec.id = s.section_id
            WHERE sec.teacher_id = %s
            ORDER BY sec.year_level ASC, sec.section_name ASC,
                     s.last_name ASC, s.first_name ASC
        """, (teacher_id,))
        students = cur.fetchall()

        teacher_name = f"{teacher['last_name']}, {teacher['first_name']}"
        if teacher["middle_name"]:
            teacher_name += f" {teacher['middle_name']}"
        if teacher["extension"]:
            teacher_name += f" {teacher['extension']}"

        return jsonify({
            "success": True,
            "teacher": {"id": teacher["id"], "name": teacher_name},
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

    errors = validate_teacher_fields(
        last_name, first_name, middle_name, extension, contact_number, email
    )
    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(url_for("teacher_bp.teachers"))

    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        try:
            cur.execute("""
                ALTER TABLE teacher_accounts 
                ALTER COLUMN password DROP NOT NULL
            """)
            conn.commit()
        except Exception as alter_error:
            print(f"Note: {alter_error}")
            conn.rollback()

        cur.execute("SELECT id FROM teachers WHERE email = %s", (email,))
        if cur.fetchone():
            flash("This email is already registered.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        cur.execute("SELECT id FROM teachers WHERE contact_number = %s", (contact_number,))
        if cur.fetchone():
            flash("This contact number is already registered.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        cur.execute("""
            INSERT INTO teachers (
                last_name, first_name, middle_name,
                extension, contact_number, email
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
        teacher_id = cur.fetchone()["id"]

        token = secrets.token_urlsafe(48)
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)

        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'teacher_accounts' 
            AND column_name IN ('reset_token', 'token_expires_at')
        """)
        existing_columns = [row['column_name'] for row in cur.fetchall()]

        if 'reset_token' not in existing_columns:
            cur.execute("ALTER TABLE teacher_accounts ADD COLUMN reset_token TEXT")
        if 'token_expires_at' not in existing_columns:
            cur.execute("ALTER TABLE teacher_accounts ADD COLUMN token_expires_at TIMESTAMP WITH TIME ZONE")

        conn.commit()

        cur.execute("SELECT id FROM teacher_accounts WHERE teacher_id = %s", (teacher_id,))
        existing = cur.fetchone()

        if existing:
            cur.execute("""
                UPDATE teacher_accounts
                SET email = %s,
                    reset_token = %s,
                    token_expires_at = %s
                WHERE teacher_id = %s
            """, (email, token, expires_at, teacher_id))
        else:
            cur.execute("""
                INSERT INTO teacher_accounts (teacher_id, email, reset_token, token_expires_at)
                VALUES (%s, %s, %s, %s)
            """, (teacher_id, email, token, expires_at))

        conn.commit()

        base_url = os.getenv("BASE_URL", request.host_url.rstrip("/"))
        reset_link = base_url + url_for("teacher_bp.create_password", token=token)
        
        try:
            send_teacher_invitation_email(
                to_email=email,
                first_name=first_name,
                last_name=last_name,
                reset_link=reset_link
            )
            flash(
                f"✅ Teacher added successfully! A password setup link has been sent to {email} (valid for 24 hours).",
                "success"
            )
        except Exception as mail_err:
            flash(
                f"⚠️ Teacher added successfully, but the invitation email could not be sent. Error: {str(mail_err)}",
                "warning"
            )
            print(f"Email sending failed: {str(mail_err)}")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error adding teacher: {str(e)}", "error")
        print(f"Teacher addition failed: {str(e)}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for("teacher_bp.teachers"))


@teacher_bp.route("/create-password/<token>", methods=["GET", "POST"])
def create_password(token):
    """Handles password creation and redirects to index on success"""
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        try:
            cur.execute("""
                ALTER TABLE teacher_accounts 
                ALTER COLUMN password DROP NOT NULL
            """)
            conn.commit()
        except:
            conn.rollback()

        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'teacher_accounts' 
            AND column_name IN ('reset_token', 'token_expires_at')
        """)
        existing_columns = [row['column_name'] for row in cur.fetchall()]

        if 'reset_token' not in existing_columns:
            cur.execute("ALTER TABLE teacher_accounts ADD COLUMN reset_token TEXT")
        if 'token_expires_at' not in existing_columns:
            cur.execute("ALTER TABLE teacher_accounts ADD COLUMN token_expires_at TIMESTAMP WITH TIME ZONE")
        conn.commit()

        cur.execute("""
            SELECT ta.id, ta.email, ta.reset_token, ta.token_expires_at,
                   t.first_name, t.last_name
            FROM teacher_accounts ta
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE ta.reset_token = %s
        """, (token,))
        account = cur.fetchone()

        if not account:
            flash("This password link is invalid or has already been used.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        expires_at = account.get("token_expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if not expires_at or datetime.now(timezone.utc) > expires_at:
            flash("This password link has expired (valid for 24 hours). Please contact your administrator for a new link.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        if request.method == "POST":
            password = request.form.get("password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()

            if not password or not confirm:
                flash("Password and confirmation are required.", "error")
                return redirect(url_for("teacher_bp.create_password", token=token))

            if password != confirm:
                flash("Passwords do not match.", "error")
                return redirect(url_for("teacher_bp.create_password", token=token))

            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return redirect(url_for("teacher_bp.create_password", token=token))

            hashed_password = generate_password_hash(password)
            cur.execute("""
                UPDATE teacher_accounts
                SET password = %s,
                    reset_token = NULL,
                    token_expires_at = NULL
                WHERE id = %s
            """, (hashed_password, account["id"]))
            conn.commit()

            # Success - redirect to index page
            flash(f"✅ Password created successfully! Welcome {account['first_name']} {account['last_name']}! You can now log in.", "success")
            return redirect(url_for("teacher_bp.teachers"))

        return render_template("superadmin/create_password.html",
                             token=token,
                             teacher_name=f"{account['first_name']} {account['last_name']}",
                             email=account['email'])

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error: {str(e)}", "error")
        return redirect(url_for("teacher_bp.teachers"))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@teacher_bp.route("/teachers/test-email")
@login_required
def test_email():
    test_to = request.args.get("email", "").strip()

    if not test_to:
        flash("Please provide an email: /teachers/test-email?email=teacher@example.com", "error")
        return redirect(url_for("teacher_bp.teachers"))

    test_token = secrets.token_urlsafe(48)
    base_url = os.getenv("BASE_URL", request.host_url.rstrip("/"))
    sample_link = base_url + url_for("teacher_bp.create_password", token=test_token)

    try:
        send_teacher_invitation_email(
            to_email=test_to,
            first_name="Test",
            last_name="Teacher",
            reset_link=sample_link
        )
        flash(f"✅ Test email sent successfully to {test_to}! Your SMTP is working correctly.", "success")
    except Exception as e:
        flash(f"❌ Failed to send test email: {str(e)}. Please check your SMTP settings in .env", "error")

    return redirect(url_for("teacher_bp.teachers"))