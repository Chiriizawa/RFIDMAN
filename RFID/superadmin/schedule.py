from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import re
from datetime import datetime, time as dtime

load_dotenv()

schedule_bp = Blueprint("schedule", __name__, template_folder="templates")


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


# ─────────────────────────────────────────────
# TIME PARSING & CONFLICT HELPERS
# ─────────────────────────────────────────────

TIME_FORMATS = [
    "%I:%M %p",   # 7:00 AM
    "%I:%M%p",    # 7:00AM
    "%H:%M",      # 07:00 (24-hr)
    "%I %p",      # 7 AM
]


def parse_time_str(raw: str):
    """
    Parse a single time string like "7:00 AM" or "07:00".
    Returns a datetime.time object or None.
    """
    raw = raw.strip().upper()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def parse_time_range(time_str: str):
    """
    Parse "7:00 AM - 8:30 AM" (or variants) into (start_time, end_time).
    Supports separators: ' - ', ' – ', '-', '–', ' to '.
    Returns (dtime, dtime) tuple or (None, None) on failure.
    """
    if not time_str:
        return None, None

    time_str = time_str.strip()

    # Try various separators
    for sep in [' - ', ' – ', ' to ', '-', '–']:
        if sep in time_str:
            parts = time_str.split(sep, 1)
            start = parse_time_str(parts[0].strip())
            end   = parse_time_str(parts[1].strip())
            if start and end and end > start:
                return start, end
            break

    return None, None


def times_overlap(start1: dtime, end1: dtime, start2: dtime, end2: dtime) -> bool:
    """
    Returns True if two time ranges overlap (exclusive endpoints).
    [start1, end1) overlaps [start2, end2) when start1 < end2 AND start2 < end1
    """
    return start1 < end2 and start2 < end1


def check_teacher_conflict_db(cur, teacher_id: int, day: str, new_start: dtime,
                               new_end: dtime, exclude_schedule_id: int = None) -> dict | None:
    """
    Check if teacher_id already has a schedule on `day` that overlaps
    the new_start–new_end range.

    Returns a conflict dict with details if conflict found, else None.
    """
    if not teacher_id:
        return None

    query = """
        SELECT sc.id, sc.day, sc.time, sc.subject,
               s.section_name,
               r.room_name
        FROM schedules sc
        LEFT JOIN sections s ON sc.section_id = s.id
        LEFT JOIN rooms    r ON sc.room_id    = r.id
        WHERE sc.teacher_id = %s
          AND sc.day        = %s
    """
    params = [teacher_id, day]

    if exclude_schedule_id:
        query += " AND sc.id != %s"
        params.append(exclude_schedule_id)

    cur.execute(query, params)
    existing = cur.fetchall()

    for sched in existing:
        existing_start, existing_end = parse_time_range(sched["time"] or "")
        if existing_start is None:
            continue
        if times_overlap(new_start, new_end, existing_start, existing_end):
            return {
                "subject":  sched["subject"],
                "section":  sched["section_name"] or "—",
                "room":     sched["room_name"] or "—",
                "time":     sched["time"],
                "day":      sched["day"],
            }

    return None


def check_room_conflict_db(cur, room_id: int, day: str, new_start: dtime,
                            new_end: dtime, exclude_schedule_id: int = None) -> dict | None:
    """
    Check if a room is already occupied during the new time slot on the given day.
    Returns conflict details or None.
    """
    if not room_id:
        return None

    query = """
        SELECT sc.id, sc.day, sc.time, sc.subject,
               s.section_name,
               CONCAT_WS(' ', t.first_name, t.middle_name, t.last_name) AS teacher
        FROM schedules sc
        LEFT JOIN sections s ON sc.section_id = s.id
        LEFT JOIN teachers t ON sc.teacher_id = t.id
        WHERE sc.room_id = %s
          AND sc.day     = %s
    """
    params = [room_id, day]

    if exclude_schedule_id:
        query += " AND sc.id != %s"
        params.append(exclude_schedule_id)

    cur.execute(query, params)
    existing = cur.fetchall()

    for sched in existing:
        existing_start, existing_end = parse_time_range(sched["time"] or "")
        if existing_start is None:
            continue
        if times_overlap(new_start, new_end, existing_start, existing_end):
            return {
                "subject": sched["subject"],
                "section": sched["section_name"] or "—",
                "teacher": sched["teacher"] or "—",
                "time":    sched["time"],
                "day":     sched["day"],
            }

    return None


# ─────────────────────────────────────────────
# VALIDATION HELPERS
# ─────────────────────────────────────────────

def validate_required_fields(**fields) -> list[str]:
    """Returns list of error messages for empty required fields."""
    errors = []
    for name, value in fields.items():
        if not value:
            errors.append(f"{name} is required.")
    return errors


def validate_time_format(time_str: str) -> tuple[dtime | None, dtime | None, str | None]:
    """
    Validates and parses a time range string.
    Returns (start_time, end_time, error_message).
    error_message is None if valid.
    """
    if not time_str or not time_str.strip():
        return None, None, "Time is required."

    start, end = parse_time_range(time_str.strip())

    if start is None or end is None:
        return None, None, (
            "Invalid time format. Use: 7:00 AM - 8:30 AM  "
            "(separate start and end with ' - ')."
        )

    if end <= start:
        return None, None, "End time must be after start time."

    return start, end, None


# ─────────────────────────────────────────────
# SCHEDULE PAGE (MAIN)
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule", methods=["GET"])
@login_required
def schedule():
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        search = request.args.get("search", "").strip()

        query = """
            SELECT
                sc.id,
                sc.day,
                sc.time,
                sc.subject,
                sc.room_id,
                sc.section_id,
                sc.teacher_id,
                r.room_name,
                s.section_name,
                s.year_level,
                CONCAT_WS(' ', t.first_name, t.middle_name, t.last_name) AS teacher
            FROM schedules sc
            LEFT JOIN rooms    r ON sc.room_id    = r.id
            LEFT JOIN sections s ON sc.section_id = s.id
            LEFT JOIN teachers t ON sc.teacher_id = t.id
        """
        params = []

        if search:
            query += """
                WHERE
                    LOWER(COALESCE(sc.subject,      '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(sc.day,       '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(r.room_name,  '')) LIKE LOWER(%s)
                    OR LOWER(COALESCE(s.section_name,'')) LIKE LOWER(%s)
            """
            like = f"%{search}%"
            params.extend([like, like, like, like])

        query += " ORDER BY sc.id ASC"
        cur.execute(query, params)
        schedules = cur.fetchall()

        cur.execute("SELECT id, room_name FROM rooms ORDER BY room_name ASC")
        rooms = cur.fetchall()

        cur.execute("SELECT id, section_name, year_level FROM sections ORDER BY section_name ASC")
        sections = cur.fetchall()

        cur.execute("""
            SELECT id, CONCAT_WS(' ', first_name, middle_name, last_name) AS name 
            FROM teachers 
            ORDER BY first_name, last_name ASC
        """)
        teachers = cur.fetchall()

        return render_template(
            "superadmin/schedule.html",
            schedules=schedules,
            rooms=rooms,
            sections=sections,
            teachers=teachers,
            total_schedules=len(schedules),
            search=search,
        )

    except Exception as e:
        flash(f"Database error: {str(e)}", "error")
        return render_template(
            "superadmin/schedule.html",
            schedules=[], rooms=[], sections=[], teachers=[],
            total_schedules=0, search="",
        )
    finally:
        if cur:  cur.close()
        if conn: conn.close()


# ─────────────────────────────────────────────
# BULK ADD SCHEDULE
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule/bulk-add", methods=["POST"])
@login_required
def bulk_add_schedule():
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        subject    = request.form.get("subject",    "").strip()
        section_id = request.form.get("section_id", "").strip()
        teacher_id = request.form.get("teacher_id", "").strip()

        # ── Validate shared fields ──────────────────────
        errors = []

        if not subject:
            errors.append("Subject is required.")
        elif len(subject) < 2:
            errors.append("Subject must be at least 2 characters.")

        if not section_id:
            errors.append("Section is required.")

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("schedule.schedule"))

        teacher_id_int = int(teacher_id) if teacher_id else None
        section_id_int = int(section_id)

        days     = request.form.getlist("days[]")
        times    = request.form.getlist("times[]")
        room_ids = request.form.getlist("room_ids[]")

        if not days:
            flash("Please add at least one day slot.", "error")
            return redirect(url_for("schedule.schedule"))

        if not (len(days) == len(times) == len(room_ids)):
            flash("Mismatched day/time/room data. Please try again.", "error")
            return redirect(url_for("schedule.schedule"))

        # ── Validate and check conflicts per row ────────
        row_errors  = []
        valid_rows  = []

        # Keep track of times already validated in this batch (for intra-batch teacher conflicts)
        # { teacher_id: { day: [(start, end, row_num)] } }
        batch_teacher_times: dict[int, dict[str, list]] = {}
        # { room_id: { day: [(start, end, row_num)] } }
        batch_room_times: dict[int, dict[str, list]] = {}

        for i, (day, time_str, room_id_str) in enumerate(zip(days, times, room_ids), start=1):
            day        = day.strip()
            time_str   = time_str.strip()
            room_id_str = room_id_str.strip()

            # Required fields check
            if not day or not time_str or not room_id_str:
                row_errors.append(f"Row {i}: Day, Time, and Room are all required.")
                continue

            # Time format validation
            start_t, end_t, time_err = validate_time_format(time_str)
            if time_err:
                row_errors.append(f"Row {i} ({day}): {time_err}")
                continue

            room_id_int = int(room_id_str)

            # ── Intra-batch teacher conflict check ──
            if teacher_id_int:
                batch_teacher_times.setdefault(teacher_id_int, {}).setdefault(day, [])
                for (existing_start, existing_end, existing_row) in batch_teacher_times[teacher_id_int][day]:
                    if times_overlap(start_t, end_t, existing_start, existing_end):
                        row_errors.append(
                            f"Row {i} ({day} {time_str}): Time conflict with Row {existing_row} "
                            f"— same teacher cannot be in two places at the same time."
                        )
                        break
                else:
                    batch_teacher_times[teacher_id_int][day].append((start_t, end_t, i))

            # ── Intra-batch room conflict check ──
            batch_room_times.setdefault(room_id_int, {}).setdefault(day, [])
            for (existing_start, existing_end, existing_row) in batch_room_times[room_id_int][day]:
                if times_overlap(start_t, end_t, existing_start, existing_end):
                    row_errors.append(
                        f"Row {i} ({day} {time_str}): Room conflict with Row {existing_row} "
                        f"— same room cannot be used by two schedules at the same time."
                    )
                    break
            else:
                batch_room_times[room_id_int][day].append((start_t, end_t, i))

            # ── DB teacher conflict check ──
            if teacher_id_int:
                conflict = check_teacher_conflict_db(cur, teacher_id_int, day, start_t, end_t)
                if conflict:
                    row_errors.append(
                        f"Row {i} ({day} {time_str}): Teacher conflict — the assigned teacher "
                        f"already has '{conflict['subject']}' ({conflict['section']}) "
                        f"in {conflict['room']} at {conflict['time']}."
                    )
                    continue

            # ── DB room conflict check ──
            room_conflict = check_room_conflict_db(cur, room_id_int, day, start_t, end_t)
            if room_conflict:
                row_errors.append(
                    f"Row {i} ({day} {time_str}): Room conflict — the room is already used for "
                    f"'{room_conflict['subject']}' ({room_conflict['section']}) "
                    f"by {room_conflict['teacher']} at {room_conflict['time']}."
                )
                continue

            valid_rows.append({
                "day":        day,
                "time":       time_str,
                "room_id":    room_id_int,
                "section_id": section_id_int,
                "teacher_id": teacher_id_int,
                "subject":    subject,
            })

        # ── If any row errors, abort everything ──
        if row_errors:
            for err in row_errors[:5]:   # show max 5 errors to avoid flooding
                flash(err, "error")
            if len(row_errors) > 5:
                flash(f"…and {len(row_errors) - 5} more error(s). Please fix all rows and try again.", "error")
            return redirect(url_for("schedule.schedule"))

        if not valid_rows:
            flash("No valid schedules to add. Please check your input.", "error")
            return redirect(url_for("schedule.schedule"))

        # ── Insert valid rows ──
        inserted = 0
        skipped  = 0

        for row in valid_rows:
            # Duplicate check
            cur.execute("""
                SELECT id FROM schedules
                WHERE day        = %s
                  AND time       = %s
                  AND section_id = %s
                  AND room_id    = %s
            """, (row["day"], row["time"], row["section_id"], row["room_id"]))

            if cur.fetchone():
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO schedules (section_id, day, time, subject, room_id, teacher_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                row["section_id"], row["day"], row["time"],
                row["subject"],    row["room_id"], row["teacher_id"],
            ))
            inserted += 1

        conn.commit()

        if inserted > 0:
            msg = f"✅ Successfully added {inserted} schedule(s) for '{subject}'."
            if skipped:
                msg += f" Skipped {skipped} duplicate(s)."
            flash(msg, "success")
        else:
            flash(
                f"No new schedules were added — all {skipped} row(s) already exist.",
                "error",
            )

    except Exception as e:
        if conn: conn.rollback()
        flash(f"Database error: {str(e)}", "error")
    finally:
        if cur:  cur.close()
        if conn: conn.close()

    return redirect(url_for("schedule.schedule"))


# ─────────────────────────────────────────────
# UPDATE SCHEDULE
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule/update/<int:schedule_id>", methods=["POST"])
@login_required
def update_schedule(schedule_id):
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        section_id_str = request.form.get("section_id", "").strip()
        day            = request.form.get("day",        "").strip()
        time_str       = request.form.get("time",       "").strip()
        subject        = request.form.get("subject",    "").strip()
        room_id_str    = request.form.get("room_id",    "").strip()
        teacher_id_str = request.form.get("teacher_id", "").strip()

        # ── Required field validation ──
        errors = []

        if not day:
            errors.append("Day is required.")

        if not subject:
            errors.append("Subject is required.")
        elif len(subject) < 2:
            errors.append("Subject must be at least 2 characters.")

        if not section_id_str:
            errors.append("Section is required.")

        if not room_id_str:
            errors.append("Room is required.")

        # ── Time format validation ──
        start_t, end_t, time_err = validate_time_format(time_str)
        if time_err:
            errors.append(f"Time: {time_err}")

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("schedule.schedule"))

        try:
            section_id = int(section_id_str)
            room_id    = int(room_id_str)
        except ValueError:
            flash("Section and Room must be valid selections.", "error")
            return redirect(url_for("schedule.schedule"))

        teacher_id = int(teacher_id_str) if teacher_id_str else None

        # ── Teacher conflict check (excluding current schedule) ──
        if teacher_id:
            conflict = check_teacher_conflict_db(
                cur, teacher_id, day, start_t, end_t,
                exclude_schedule_id=schedule_id
            )
            if conflict:
                flash(
                    f"Teacher conflict: the assigned teacher already has "
                    f"'{conflict['subject']}' ({conflict['section']}) "
                    f"in {conflict['room']} on {conflict['day']} at {conflict['time']}. "
                    f"Please choose a different time or teacher.",
                    "error",
                )
                return redirect(url_for("schedule.schedule"))

        # ── Room conflict check (excluding current schedule) ──
        room_conflict = check_room_conflict_db(
            cur, room_id, day, start_t, end_t,
            exclude_schedule_id=schedule_id
        )
        if room_conflict:
            flash(
                f"Room conflict: this room is already used for "
                f"'{room_conflict['subject']}' ({room_conflict['section']}) "
                f"by {room_conflict['teacher']} on {room_conflict['day']} at {room_conflict['time']}. "
                f"Please select a different room or time.",
                "error",
            )
            return redirect(url_for("schedule.schedule"))

        # ── Perform update ──
        cur.execute("""
            UPDATE schedules
            SET section_id = %s,
                day        = %s,
                time       = %s,
                subject    = %s,
                room_id    = %s,
                teacher_id = %s
            WHERE id = %s
        """, (section_id, day, time_str, subject, room_id, teacher_id, schedule_id))

        conn.commit()
        flash("Schedule updated successfully.", "success")

    except Exception as e:
        if conn: conn.rollback()
        flash(f"Database error: {str(e)}", "error")
    finally:
        if cur:  cur.close()
        if conn: conn.close()

    return redirect(url_for("schedule.schedule"))


# ─────────────────────────────────────────────
# DELETE SCHEDULE
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule/delete/<int:schedule_id>", methods=["POST"])
@login_required
def delete_schedule(schedule_id):
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
        conn.commit()

        flash("Schedule deleted successfully.", "success")

    except Exception as e:
        if conn: conn.rollback()
        flash(f"Database error: {str(e)}", "error")
    finally:
        if cur:  cur.close()
        if conn: conn.close()

    return redirect(url_for("schedule.schedule"))


# ─────────────────────────────────────────────
# ADD ROOM
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule/room/add", methods=["POST"])
@login_required
def add_room():
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        room_name = request.form.get("room_name", "").strip()

        # ── Validation ──
        if not room_name:
            flash("Room name is required.", "error")
            return redirect(url_for("schedule.schedule"))

        if len(room_name) < 2:
            flash("Room name must be at least 2 characters.", "error")
            return redirect(url_for("schedule.schedule"))

        if len(room_name) > 60:
            flash("Room name must not exceed 60 characters.", "error")
            return redirect(url_for("schedule.schedule"))

        # ── Duplicate check ──
        cur.execute(
            "SELECT id FROM rooms WHERE LOWER(room_name) = LOWER(%s)", (room_name,)
        )
        if cur.fetchone():
            flash(f'Room "{room_name}" already exists.', "error")
            return redirect(url_for("schedule.schedule"))

        cur.execute("INSERT INTO rooms (room_name) VALUES (%s)", (room_name,))
        conn.commit()

        flash(f'Room "{room_name}" added successfully.', "success")

    except Exception as e:
        if conn: conn.rollback()
        flash(f"Database error: {str(e)}", "error")
    finally:
        if cur:  cur.close()
        if conn: conn.close()

    return redirect(url_for("schedule.schedule"))


# ─────────────────────────────────────────────
# DELETE ROOM
# ─────────────────────────────────────────────

@schedule_bp.route("/schedule/room/delete/<int:room_id>", methods=["POST"])
@login_required
def delete_room(room_id):
    conn = None
    cur  = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM schedules WHERE room_id = %s", (room_id,)
        )
        if cur.fetchone()["cnt"] > 0:
            flash("Cannot delete room — it is used in existing schedules.", "error")
            return redirect(url_for("schedule.schedule"))

        cur.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
        conn.commit()

        flash("Room deleted successfully.", "success")

    except Exception as e:
        if conn: conn.rollback()
        flash(f"Database error: {str(e)}", "error")
    finally:
        if cur:  cur.close()
        if conn: conn.close()

    return redirect(url_for("schedule.schedule"))