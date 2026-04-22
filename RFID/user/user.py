from flask import Blueprint, render_template, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

load_dotenv()

user = Blueprint("user", __name__, template_folder="template")


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432)
    )


def format_full_name(first_name, middle_name, last_name, extension):
    parts = [
        first_name.strip() if first_name else "",
        middle_name.strip() if middle_name else "",
        last_name.strip() if last_name else "",
        extension.strip() if extension else ""
    ]
    return " ".join([p for p in parts if p]).strip()


@user.route('/')
def index():
    return render_template('user/index.html')


@user.route('/get_latest_tap')
def get_latest_tap():
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                r.uid,
                r.created_at,
                s.first_name,
                s.middle_name,
                s.last_name,
                s.extension
            FROM rfid_cards r
            LEFT JOIN students s
                ON UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(r.uid, '')), ' ', ''), '-', ''), ':', ''))
                = UPPER(REPLACE(REPLACE(REPLACE(TRIM(COALESCE(s.uid, '')), ' ', ''), '-', ''), ':', ''))
            ORDER BY r.created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()

        if not row:
            return jsonify({
                "success": True,
                "name": "Waiting for scan...",
                "uid": "",
                "time": ""
            })

        full_name = format_full_name(
            row.get("first_name"),
            row.get("middle_name"),
            row.get("last_name"),
            row.get("extension")
        )

        return jsonify({
            "success": True,
            "name": full_name if full_name else "No linked student",
            "uid": row.get("uid", ""),
            "time": row["created_at"].strftime("%Y-%m-%d %I:%M:%S %p") if row.get("created_at") else ""
        })

    except Exception as e:
        print("User latest tap error:", e)
        return jsonify({
            "success": False,
            "name": "Error loading scan",
            "uid": "",
            "time": ""
        })

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()