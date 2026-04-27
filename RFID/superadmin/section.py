from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import re

load_dotenv()

section_bp = Blueprint("section_bp", __name__, template_folder="template")


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


@section_bp.route("/Section", methods=["GET", "POST"])
@login_required
def section():
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if request.method == "POST":
            action = request.form.get("action", "").strip()

            # ─────────────────────────────────────────────
            # ADD SECTION
            # ─────────────────────────────────────────────
            if action == "add_section":
                section_name = request.form.get("section_name", "").strip()
                year_level = request.form.get("year_level", "").strip()
                teacher_id = request.form.get("teacher_id", "").strip()

                if not section_name:
                    flash("Section name is required.", "error")
                    return redirect(url_for("section_bp.section"))

                if not re.match(r'^[A-Za-z]{3,20}$', section_name):
                    flash("Section name must contain only letters (A-Z, a-z), no numbers or spaces, and be 3-20 characters long.", "error")
                    return redirect(url_for("section_bp.section"))

                if year_level and year_level not in ["11", "12"]:
                    flash("Year level must be 11 or 12.", "error")
                    return redirect(url_for("section_bp.section"))

                # === NEW: Check duplicate (name + year_level) ===
                cur.execute("""
                    SELECT id FROM sections 
                    WHERE LOWER(section_name) = LOWER(%s) 
                    AND (year_level = %s OR (year_level IS NULL AND %s IS NULL))
                """, (section_name, year_level if year_level else None, year_level if year_level else None))
                if cur.fetchone():
                    flash(f'A section named "{section_name}" already exists in Grade {year_level or "—"}.', "error")
                    return redirect(url_for("section_bp.section"))
                # === END CHECK ===

                if teacher_id == "":
                    teacher_id = None
                else:
                    teacher_id = int(teacher_id)

                cur.execute("""
                    INSERT INTO sections (section_name, year_level, teacher_id)
                    VALUES (%s, %s, %s)
                """, (section_name, year_level if year_level else None, teacher_id))
                conn.commit()

                flash("Section added successfully.", "success")
                return redirect(url_for("section_bp.section"))

            # ─────────────────────────────────────────────
            # EDIT SECTION
            # ─────────────────────────────────────────────
            elif action == "edit_section":
                section_id = request.form.get("section_id", "").strip()
                section_name = request.form.get("section_name", "").strip()
                year_level = request.form.get("year_level", "").strip()
                teacher_id = request.form.get("teacher_id", "").strip()

                if not section_id or not section_name:
                    flash("Section ID and name are required.", "error")
                    return redirect(url_for("section_bp.section"))

                if not re.match(r'^[A-Za-z]{3,20}$', section_name):
                    flash("Section name must contain only letters (A-Z, a-z), no numbers or spaces, and be 3-20 characters long.", "error")
                    return redirect(url_for("section_bp.section"))

                if year_level and year_level not in ["11", "12"]:
                    flash("Year level must be 11 or 12.", "error")
                    return redirect(url_for("section_bp.section"))

                # === NEW: Check duplicate (ignore current section) ===
                cur.execute("""
                    SELECT id FROM sections 
                    WHERE LOWER(section_name) = LOWER(%s) 
                    AND (year_level = %s OR (year_level IS NULL AND %s IS NULL))
                    AND id != %s
                """, (section_name, year_level if year_level else None, year_level if year_level else None, section_id))
                if cur.fetchone():
                    flash(f'A section named "{section_name}" already exists in Grade {year_level or "—"}.', "error")
                    return redirect(url_for("section_bp.section"))
                # === END CHECK ===

                if teacher_id == "":
                    teacher_id = None
                else:
                    teacher_id = int(teacher_id)

                cur.execute("""
                    UPDATE sections
                    SET section_name = %s,
                        year_level = %s,
                        teacher_id = %s
                    WHERE id = %s
                """, (
                    section_name,
                    year_level if year_level else None,
                    teacher_id,
                    section_id
                ))
                conn.commit()

                flash("Section updated successfully.", "success")
                return redirect(url_for("section_bp.section"))

            # DELETE and ASSIGN STUDENTS (unchanged)
            elif action == "delete_section":
                section_id = request.form.get("section_id", "").strip()
                if not section_id:
                    flash("Section ID is required.", "error")
                    return redirect(url_for("section_bp.section"))

                cur.execute("DELETE FROM sections WHERE id = %s", (section_id,))
                conn.commit()
                flash("Section deleted successfully.", "success")
                return redirect(url_for("section_bp.section"))

            elif action == "assign_students":
                section_id = request.form.get("section_id", "").strip()
                student_ids = request.form.getlist("student_ids")

                if not section_id:
                    flash("Section ID is required.", "error")
                    return redirect(url_for("section_bp.section"))

                try:
                    section_id = int(section_id)
                    selected_ids = [int(sid) for sid in student_ids if sid.strip()]
                except ValueError:
                    flash("Invalid ID format.", "error")
                    return redirect(url_for("section_bp.section"))

                cur.execute("UPDATE students SET section_id = NULL WHERE section_id = %s", (section_id,))

                if selected_ids:
                    cur.execute(
                        "UPDATE students SET section_id = %s WHERE id = ANY(%s)",
                        (section_id, selected_ids)
                    )
                    flash(f"{len(selected_ids)} student(s) successfully assigned.", "success")
                else:
                    flash("All students have been unassigned from this section.", "success")

                conn.commit()
                return redirect(url_for("section_bp.section"))

        # GET REQUEST (unchanged - no need to modify)
        search = request.args.get("search", "").strip()

        query = """
            SELECT
                s.id,
                s.section_name,
                s.year_level,
                s.teacher_id,
                s.created_at,
                CASE
                    WHEN t.id IS NOT NULL THEN
                        CONCAT(
                            t.last_name, ', ',
                            t.first_name,
                            CASE
                                WHEN t.middle_name IS NOT NULL AND t.middle_name <> '' THEN ' ' || t.middle_name
                                ELSE ''
                            END
                        )
                    ELSE NULL
                END AS adviser_name,
                COUNT(st.id) AS total_students
            FROM sections s
            LEFT JOIN teachers t ON s.teacher_id = t.id
            LEFT JOIN students st ON s.id = st.section_id
        """

        params = []
        if search:
            query += """
                WHERE
                    LOWER(COALESCE(s.section_name, '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(s.year_level::text, '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(t.first_name, '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(t.middle_name, '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(t.last_name, '')) LIKE LOWER(%s)
            """
            like_search = f"%{search}%"
            params.extend([like_search] * 5)

        query += """
            GROUP BY
                s.id, s.section_name, s.year_level, s.teacher_id, s.created_at,
                t.id, t.first_name, t.middle_name, t.last_name
            ORDER BY s.id ASC
        """

        cur.execute(query, params)
        sections = cur.fetchall()

        cur.execute("""
            SELECT id, first_name, middle_name, last_name, email
            FROM teachers
            ORDER BY last_name ASC, first_name ASC
        """)
        teachers = cur.fetchall()

        cur.execute("""
            SELECT 
                s.id,
                s.first_name,
                s.middle_name,
                s.last_name,
                s.section_id,
                (SELECT section_name FROM sections WHERE id = s.section_id) AS current_section_name
            FROM students s
            ORDER BY s.last_name ASC, s.first_name ASC
        """)
        all_students = cur.fetchall()

        total_sections = len(sections)

        return render_template(
            "superadmin/section.html",
            sections=sections,
            teachers=teachers,
            all_students=all_students,
            total_sections=total_sections,
            search=search
        )

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Database error: {str(e)}", "error")
        return render_template(
            "superadmin/section.html",
            sections=[], teachers=[], all_students=[], total_sections=0, search=""
        )

    finally:
        if cur: cur.close()
        if conn: conn.close()