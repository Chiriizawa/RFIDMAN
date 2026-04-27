from flask import Blueprint, jsonify, render_template, request
from datetime import datetime
import os
import re
import threading
import time
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse

# Try to import serial
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("[WARNING] pyserial not installed. Run: pip install pyserial")

rfid_bp = Blueprint('rfid', __name__, template_folder='template')

# Shared state
_rfid_data = {'uid': 'Waiting for RFID card...', 'last_insert': None}
_db_config = {}

# Serial control
_serial_port = None
_serial_lock = threading.Lock()
_serial_thread = None
_stop_event = threading.Event()
_DEBOUNCE_SECONDS = 2
_last_uid_seen = None
_last_uid_time = 0.0

def _close_serial():
    """Close serial port"""
    global _serial_port
    with _serial_lock:
        if _serial_port and _serial_port.is_open:
            try:
                _serial_port.close()
                print("[Serial] Port closed")
            except:
                pass
            _serial_port = None

def set_rfid_data(data: dict):
    global _rfid_data
    _rfid_data = data

def set_db_config(config: dict):
    global _db_config
    _db_config = config

def get_db_connection():
    """Get database connection from DATABASE_URL"""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        return psycopg2.connect(
            host=_db_config.get('DB_HOST', 'localhost'),
            port=_db_config.get('DB_PORT', 5432),
            dbname=_db_config.get('DB_NAME', 'postgres'),
            user=_db_config.get('DB_USER', 'postgres'),
            password=_db_config.get('DB_PASSWORD', ''),
        )
    
    parsed = urlparse(database_url)
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip('/'),
        user=parsed.username,
        password=parsed.password,
        sslmode='require'
    )

def init_db():
    """Initialize database tables"""
    print("[DB] Initializing database...")
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rfid_cards (
                        id         SERIAL PRIMARY KEY,
                        uid        VARCHAR(50)  NOT NULL,
                        tapped_at  TIMESTAMP    NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rfid_cards_uid_tapped 
                    ON rfid_cards(uid, DATE(tapped_at));
                """)
        print("[DB] rfid_cards table ready.")
        conn.close()
    except Exception as e:
        print(f"[DB] Init error: {e}")

def insert_rfid_card(uid: str) -> dict:
    """Insert a UID into rfid_cards"""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO rfid_cards (uid, tapped_at)
                    VALUES (%s, NOW())
                    RETURNING id, uid, tapped_at
                    """,
                    (uid,)
                )
                row = cur.fetchone()
                print(f"[DB] Inserted: {uid}")
                return {'id': row['id'], 'uid': row['uid'], 'tapped_at': str(row['tapped_at'])}
    except Exception as e:
        print(f"[DB] Insert error: {e}")
        raise
    finally:
        conn.close()

def find_serial_port():
    """Find the RFID serial port"""
    # Check environment variable first
    manual_port = os.environ.get('RFID_PORT')
    if manual_port:
        print(f"[Serial] Using manual port from .env: {manual_port}")
        return manual_port
    
    if not SERIAL_AVAILABLE:
        return None
    
    # Try common ports
    for port in ['COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM3']:
        try:
            test_ser = serial.Serial(port, 9600, timeout=0.3)
            test_ser.close()
            print(f"[Serial] Found working port: {port}")
            return port
        except Exception as e:
            continue
    
    # Auto-detect
    for port in serial.tools.list_ports.comports():
        desc = port.description.lower()
        if any(k in desc for k in ('arduino', 'ch340', 'cp210', 'usb serial', 'rfid', 'serial')):
            print(f"[Serial] Auto-detected: {port.device}")
            return port.device
    
    return None

def serial_reader_thread():
    """Background thread to read RFID tags"""
    global _serial_port, _last_uid_seen, _last_uid_time, _rfid_data
    
    port_name = find_serial_port()
    if not port_name:
        print("[Serial] No RFID reader found.")
        print("[Serial] Make sure:")
        print("   1. RFID reader is connected via USB")
        print("   2. Correct COM port is set in .env file (RFID_PORT=COMx)")
        print("   3. No other program is using the COM port")
        return
    
    print(f"[Serial] Attempting to connect to {port_name}...")
    
    while not _stop_event.is_set():
        try:
            # Try to open port
            with _serial_lock:
                _serial_port = serial.Serial(port_name, 9600, timeout=1)
            
            print(f"[Serial] ✅ Connected to {port_name} - Ready for RFID tags!")
            print(f"[Serial] Listening for card taps...")
            
            while not _stop_event.is_set():
                if _serial_port and _serial_port.in_waiting:
                    line = _serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"[RFID] Raw data: {line}")
                        
                        # Look for 8-character hex UID
                        uid_match = re.search(r'([0-9A-Fa-f]{8})', line)
                        if uid_match:
                            uid = uid_match.group(1).upper()
                            
                            # Debounce
                            now = time.time()
                            if uid == _last_uid_seen and (now - _last_uid_time) < _DEBOUNCE_SECONDS:
                                print(f"[RFID] Skipped duplicate: {uid}")
                                continue
                            
                            _last_uid_seen = uid
                            _last_uid_time = now
                            
                            print(f"[RFID] ✅ CARD DETECTED! UID: {uid}")
                            _rfid_data['uid'] = uid
                            
                            try:
                                record = insert_rfid_card(uid)
                                _rfid_data['last_insert'] = record
                                print(f"[RFID] ✅ Saved to database")
                            except Exception as db_err:
                                print(f"[RFID] Database error: {db_err}")
                
                time.sleep(0.05)
        
        except serial.SerialException as e:
            error_msg = str(e)
            if "Access is denied" in error_msg or "PermissionError" in error_msg:
                print(f"[Serial] ❌ Port {port_name} is in use by another program!")
                print(f"[Serial] Please close other programs using the COM port")
            else:
                print(f"[Serial] ❌ Error: {e}")
            print(f"[Serial] Retrying in 5 seconds...")
            _close_serial()
            time.sleep(5)
        except Exception as e:
            print(f"[Serial] Unexpected error: {e}")
            time.sleep(5)
        finally:
            _close_serial()

def start_serial_reader():
    """Start the RFID reader thread"""
    global _serial_thread
    
    _stop_event.clear()
    _serial_thread = threading.Thread(target=serial_reader_thread, daemon=True)
    _serial_thread.start()
    print("[RFID] Reader thread started")

# ============= ROUTES =============

@rfid_bp.route('/')
def index():
    return render_template('user/index.html')

@rfid_bp.route('/status')
def status():
    return jsonify({
        'uid': _rfid_data.get('uid', 'Waiting for RFID card...'),
        'last_insert': _rfid_data.get('last_insert'),
        'timestamp': datetime.now().isoformat()
    })

@rfid_bp.route('/stats')
def stats():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            today = datetime.now().strftime('%Y-%m-%d')
            cur.execute("""
                SELECT COUNT(*) as total_taps, COUNT(DISTINCT uid) as unique_students
                FROM rfid_cards WHERE DATE(tapped_at) = %s
            """, (today,))
            row = cur.fetchone()
            return jsonify({
                'success': True,
                'total_taps': row[0] if row[0] else 0,
                'unique_students': row[1] if row[1] else 0
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@rfid_bp.route('/tap/<uid>', methods=['POST'])
def manual_tap(uid):
    uid = uid.upper().strip()
    if not uid or not re.match(r'^[0-9A-F]{8}$', uid):
        return jsonify({'error': 'Invalid UID format'}), 400
    
    try:
        record = insert_rfid_card(uid)
        _rfid_data['uid'] = uid
        _rfid_data['last_insert'] = record
        print(f"[Manual] Tap: {uid}")
        return jsonify({'success': True, 'record': record}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@rfid_bp.route('/simulate/<uid>')
def simulate_tap(uid):
    uid = uid.upper().strip()
    if not re.match(r'^[0-9A-F]{8}$', uid):
        return f"Invalid UID: {uid}"
    
    try:
        record = insert_rfid_card(uid)
        _rfid_data['uid'] = uid
        _rfid_data['last_insert'] = record
        return f"✅ Tap recorded: {uid}"
    except Exception as e:
        return f"Error: {e}"

@rfid_bp.route('/history')
def history():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, uid, tapped_at FROM rfid_cards ORDER BY tapped_at DESC LIMIT 20")
            rows = cur.fetchall()
            return jsonify({'success': True, 'history': [dict(row) for row in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@rfid_bp.route('/clear')
def clear():
    _rfid_data['uid'] = 'Waiting for RFID card...'
    _rfid_data['last_insert'] = None
    return jsonify({'success': True})

@rfid_bp.route('/debug')
def debug():
    """Debug endpoint"""
    ports = []
    if SERIAL_AVAILABLE:
        for port in serial.tools.list_ports.comports():
            ports.append({
                'device': port.device,
                'description': port.description
            })
    return jsonify({
        'serial_available': SERIAL_AVAILABLE,
        'available_ports': ports,
        'env_rfid_port': os.environ.get('RFID_PORT'),
        'current_uid': _rfid_data.get('uid')
    })

print("[user.py] Loaded - RFID Ready")