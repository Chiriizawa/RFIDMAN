from flask import Blueprint, jsonify, render_template
from datetime import datetime
import os
import re
import threading
import time
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
import serial

rfid_bp = Blueprint('rfid', __name__, template_folder='template')

# Data storage
_rfid_data = {'uid': 'Waiting...', 'last_insert': None}
_db_config = {}

# Serial
_serial_port = None
_stop_event = threading.Event()

def _close_serial():
    global _serial_port
    if _serial_port and _serial_port.is_open:
        try:
            _serial_port.close()
            print("[Serial] Closed")
        except:
            pass

def set_rfid_data(data):
    global _rfid_data
    _rfid_data = data

def set_db_config(config):
    global _db_config
    _db_config = config

def get_db_connection():
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        p = urlparse(db_url)
        return psycopg2.connect(
            host=p.hostname, port=p.port or 5432,
            dbname=p.path.lstrip('/'), user=p.username,
            password=p.password, sslmode='require'
        )
    return psycopg2.connect(**_db_config)

def init_db():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rfid_cards (
                    id SERIAL PRIMARY KEY,
                    uid VARCHAR(50) NOT NULL,
                    tapped_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
        conn.close()
        print("[DB] Ready")
    except Exception as e:
        print(f"[DB] Error: {e}")

def save_card(uid):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "INSERT INTO rfid_cards (uid) VALUES (%s) RETURNING id, uid, tapped_at",
                (uid,)
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    finally:
        conn.close()

def reader_thread():
    global _serial_port, _rfid_data
    
    port = os.environ.get('RFID_PORT')
    if not port:
        print("[RFID] No RFID_PORT in .env")
        return
    
    print(f"[RFID] Connecting to {port}...")
    
    while not _stop_event.is_set():
        try:
            _serial_port = serial.Serial(port, 9600, timeout=1)
            print(f"[RFID] ✅ Connected! Tap your card...")
            
            while not _stop_event.is_set():
                if _serial_port.in_waiting:
                    line = _serial_port.readline().decode().strip()
                    if line and re.match(r'^[0-9A-F]{8}$', line.upper()):
                        uid = line.upper()
                        print(f"[RFID] 🎉 Card: {uid}")
                        _rfid_data['uid'] = uid
                        _rfid_data['last_insert'] = save_card(uid)
                time.sleep(0.05)
                
        except Exception as e:
            print(f"[RFID] Error: {e}")
            time.sleep(5)
        finally:
            _close_serial()

def start_serial_reader():
    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()
    print("[RFID] Started")

# Routes
@rfid_bp.route('/')
def index():
    return render_template('user/index.html')

@rfid_bp.route('/status')
def status():
    return jsonify(_rfid_data)

@rfid_bp.route('/stats')
def stats():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rfid_cards WHERE DATE(tapped_at) = CURRENT_DATE")
            total = cur.fetchone()[0]
            return jsonify({'success': True, 'total_taps': total})
    except:
        return jsonify({'success': False, 'total_taps': 0})
    finally:
        conn.close()

@rfid_bp.route('/history')
def history():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, uid, tapped_at FROM rfid_cards ORDER BY tapped_at DESC LIMIT 30")
            return jsonify({'success': True, 'history': [dict(r) for r in cur.fetchall()]})
    except:
        return jsonify({'success': False, 'history': []})
    finally:
        conn.close()

print("[RFID] Module loaded")
@rfid_bp.route('/tap_relay/<uid>', methods=['POST'])
def tap_relay(uid):
    """Receive taps from the relay script"""
    uid = uid.upper().strip()
    if not re.match(r'^[0-9A-F]{8}$', uid):
        return jsonify({'error': 'Invalid UID'}), 400
    
    try:
        record = insert_rfid_card(uid)
        _rfid_data['uid'] = uid
        _rfid_data['last_insert'] = record
        print(f"[Relay] ✅ Saved: {uid}")
        return jsonify({'success': True, 'record': record}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@rfid_bp.route('/rooms')
def get_rooms():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, room_name, created_at FROM rooms ORDER BY room_name")
            rooms = cur.fetchall()
            return jsonify({
                'success': True,
                'rooms': [dict(room) for room in rooms]
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()