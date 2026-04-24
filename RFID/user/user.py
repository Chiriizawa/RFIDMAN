from flask import Blueprint, render_template, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

load_dotenv()

user = Blueprint("user", __name__, template_folder="template")


# DB CONNECTION
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432)
    )


# FULL NAME FORMATTER
def format_full_name(first, middle, last, ext):
    return " ".join(filter(None, [first, middle, last, ext]))


# FRONTEND PAGE
@user.route('/')
def index():
    return render_template('user/index.html')


# TEST ROUTE (CHECK IF WORKING)
@user.route('/test')
def test():
    return "USER BLUEPRINT WORKING"


# MAIN RFID FETCH
@user.route('/get_latest_tap')
def get_latest_tap():
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
                ON REPLACE(LOWER(r.uid), ' ', '') = REPLACE(LOWER(s.uid), ' ', '')
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
            row["first_name"],
            row["middle_name"],
            row["last_name"],
            row["extension"]
        )

        return jsonify({
            "success": True,
            "name": full_name if full_name else "Unknown Student",
            "uid": row["uid"],
            "time": row["created_at"].strftime("%Y-%m-%d %I:%M:%S %p")
        })

    except Exception as e:
        print("ERROR:", e)
        return jsonify({
            "success": False,
            "name": "Error loading data",
            "uid": "",
            "time": ""
        })

    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()