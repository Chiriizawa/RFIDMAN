from flask import Blueprint, render_template, request, redirect, url_for, flash
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import smtplib
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.security import generate_password_hash

load_dotenv()

teacher_bp = Blueprint("teacher_bp", __name__, template_folder="template")


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432)
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

                <p style="font-size:14px;color:#6b7280;line-height:1.6;margin-top:24px;">
                    If you did not expect this email, you may ignore it.
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
                   birthday,
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


@teacher_bp.route("/teachers/add", methods=["POST"])
def add_teacher():
    last_name = request.form.get("last_name", "").strip()
    first_name = request.form.get("first_name", "").strip()
    middle_name = request.form.get("middle_name", "").strip()
    extension = request.form.get("extension", "").strip()
    birthday = request.form.get("birthday", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    email = request.form.get("email", "").strip()

    if not last_name or not first_name or not birthday or not contact_number or not email:
        flash("Last Name, First Name, Birthday, Contact Number, and Email are required.", "error")
        return redirect(url_for("teacher_bp.teachers"))

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO teachers (
                last_name,
                first_name,
                middle_name,
                extension,
                birthday,
                contact_number,
                email
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            last_name,
            first_name,
            middle_name if middle_name else None,
            extension if extension else None,
            birthday,
            contact_number,
            email
        ))

        new_teacher_db_id = cur.fetchone()[0]
        reset_token = secrets.token_urlsafe(32)

        cur.execute("""
            INSERT INTO teacher_accounts (
                teacher_id,
                email,
                password,
                reset_token
            )
            VALUES (%s, %s, %s, %s)
        """, (
            new_teacher_db_id,
            email,
            "",
            reset_token
        ))

        conn.commit()

        reset_link = request.host_url.rstrip("/") + url_for("teacher_bp.create_password", token=reset_token)

        try:
            send_teacher_create_password_email(
                to_email=email,
                first_name=first_name,
                last_name=last_name,
                reset_link=reset_link
            )
            flash("Teacher added successfully, account created, and create-password email sent.", "success")
        except Exception as email_error:
            flash(f"Teacher added and account created, but email failed: {str(email_error)}", "error")

    except psycopg2.errors.UniqueViolation:
        if conn:
            conn.rollback()
        flash("Email already exists.", "error")

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


@teacher_bp.route("/teachers/send-create-password/<int:teacher_id>")
def send_create_password(teacher_id):
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT t.id,
                   t.first_name,
                   t.last_name,
                   t.email,
                   ta.id AS account_id
            FROM teachers t
            JOIN teacher_accounts ta ON ta.teacher_id = t.id
            WHERE t.id = %s
        """, (teacher_id,))
        teacher = cur.fetchone()

        if not teacher:
            flash("Teacher account not found.", "error")
            return redirect(url_for("teacher_bp.teachers"))

        reset_token = secrets.token_urlsafe(32)

        cur.execute("""
            UPDATE teacher_accounts
            SET reset_token = %s
            WHERE teacher_id = %s
        """, (reset_token, teacher_id))

        conn.commit()

        reset_link = request.host_url.rstrip("/") + url_for("teacher_bp.create_password", token=reset_token)

        send_teacher_create_password_email(
            to_email=teacher["email"],
            first_name=teacher["first_name"],
            last_name=teacher["last_name"],
            reset_link=reset_link
        )

        flash("Create password email sent successfully.", "success")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Error sending create password email: {str(e)}", "error")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for("teacher_bp.teachers"))


@teacher_bp.route("/create-password/<token>", methods=["GET", "POST"])
def create_password(token):
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
            return render_template("teacher/create_password.html", token=token)

        if request.method == "POST":
            password = request.form.get("password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            if not password or not confirm_password:
                flash("Password and Confirm Password are required.", "error")
                return render_template("teacher/create_password.html", token=token)

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("teacher/create_password.html", token=token)

            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return render_template("teacher/create_password.html", token=token)

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