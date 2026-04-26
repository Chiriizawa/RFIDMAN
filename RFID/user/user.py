from flask import Blueprint, jsonify, render_template
import psycopg2
import threading
import serial
import serial.tools.list_ports
import time
import atexit
import signal
import sys
import re

rfid_bp = Blueprint('rfid', __name__, template_folder='template')

# ── Shared state (injected from create_app) ──────────────────────────────────
_rfid_data = {'uid': 'Waiting for RFID card...'}
_db_config  = {}

# ── Serial control ────────────────────────────────────────────────────────────
_stop_event  = threading.Event()
_serial_port = None
_serial_lock = threading.Lock()

# ── Duplicate suppression ─────────────────────────────────────────────────────
# If the same UID is seen again within this many seconds, skip it
_DEBOUNCE_SECONDS = 3
_last_uid_seen    = None
_last_uid_time    = 0.0

def set_rfid_data(data: dict):
    global _rfid_data
    _rfid_data = data

def set_db_config(config: dict):
    global _db_config
    _db_config = config


# ── Database helpers ──────────────────────────────────────────────────────────
def get_connection():
    return psycopg2.connect(
        host     = _db_config['DB_HOST'],
        port     = _db_config['DB_PORT'],
        dbname   = _db_config['DB_NAME'],
        user     = _db_config['DB_USER'],
        password = _db_config['DB_PASSWORD'],
    )


def init_db():
    """Create rfid_cards table if it doesn't exist."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rfid_cards (
                        id         SERIAL PRIMARY KEY,
                        uid        VARCHAR(50)  NOT NULL,
                        tapped_at  TIMESTAMP    NOT NULL DEFAULT NOW()
                    );
                """)
        print("[DB] rfid_cards table ready.")
    finally:
        conn.close()


def insert_rfid_card(uid: str) -> dict:
    """Insert a UID into rfid_cards and return the new row."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rfid_cards (uid)
                    VALUES (%s)
                    RETURNING id, uid, tapped_at
                    """,
                    (uid,)
                )
                row = cur.fetchone()
                return {'id': row[0], 'uid': row[1], 'tapped_at': str(row[2])}
    finally:
        conn.close()


# ── Serial reader (background thread) ────────────────────────────────────────
def _find_serial_port() -> str | None:
    """Auto-detect the first likely Arduino/RFID COM port."""
    for port in serial.tools.list_ports.comports():
        desc = (port.description or '').lower()
        if any(k in desc for k in ('arduino', 'ch340', 'cp210', 'usb serial', 'rfid')):
            return port.device
    return None


def _close_serial():
    """Safely close the serial port — called on shutdown."""
    global _serial_port
    with _serial_lock:
        if _serial_port and _serial_port.is_open:
            try:
                _serial_port.close()
                print("[Serial] Port closed cleanly.")
            except Exception as e:
                print(f"[Serial] Error closing port: {e}")
            _serial_port = None


def _serial_reader_loop():
    global _serial_port, _last_uid_seen, _last_uid_time

    port_name = _find_serial_port()
    if not port_name:
        print("[Serial] No RFID serial port detected — reader thread idle.")
        return

    print(f"[Serial] Connecting to {port_name} …")

    while not _stop_event.is_set():
        try:
            with _serial_lock:
                ser = serial.Serial(port_name, 9600, timeout=1)
                _serial_port = ser

            print(f"[Serial] Listening on {port_name}")

            while not _stop_event.is_set():
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                # ── Extract ONLY the 8-character hex UID from the line ──────
                # Handles: "03142239", "RFID:03142239", "CARD DETECTED! UID: 03142239"
                # Ignores:  "hello", "RFID Ready", "Scanning..." (no 8-hex match)
                uid_match = re.search(r'\b([0-9A-Fa-f]{8})\b', line)
                if not uid_match:
                    print(f"[RFID] Ignored: {line!r}")
                    continue

                uid = uid_match.group(1).upper()

                # ── Debounce: skip if same UID seen within debounce window ──
                now = time.time()
                if uid == _last_uid_seen and (now - _last_uid_time) < _DEBOUNCE_SECONDS:
                    print(f"[RFID] Debounced duplicate: {uid}")
                    continue

                # ── New unique scan — record it ──────────────────────────────
                _last_uid_seen = uid
                _last_uid_time = now

                print(f"[RFID] Tapped: {uid}")
                _rfid_data['uid'] = uid

                try:
                    record = insert_rfid_card(uid)
                    _rfid_data['last_insert'] = record
                    print(f"[DB] Inserted → {record}")
                except Exception as db_err:
                    print(f"[DB] Insert error: {db_err}")

        except serial.SerialException as e:
            print(f"[Serial] Error: {e} — retrying in 5s …")
            _close_serial()
            for _ in range(50):
                if _stop_event.is_set():
                    break
                time.sleep(0.1)
        finally:
            _close_serial()


def _shutdown_handler(*args):
    """Called on Ctrl+C or process exit — releases COM port."""
    print("\n[Serial] Shutting down, releasing port …")
    _stop_event.set()
    _close_serial()
    sys.exit(0)


def start_serial_reader():
    atexit.register(_close_serial)
    signal.signal(signal.SIGINT,  _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    _stop_event.clear()
    t = threading.Thread(target=_serial_reader_loop, daemon=True, name="rfid-serial")
    t.start()


# ── Routes ────────────────────────────────────────────────────────────────────
@rfid_bp.route('/')
def index():
    return render_template('user/index.html')


@rfid_bp.route('/status')
def status():
    """Return current RFID state as JSON (polled by the frontend)."""
    return jsonify({
        'uid':         _rfid_data.get('uid', 'Waiting for RFID card...'),
        'last_insert': _rfid_data.get('last_insert'),
    })


@rfid_bp.route('/tap/<uid>', methods=['POST'])
def manual_tap(uid: str):
    """
    Manual tap endpoint — useful for testing without physical hardware.
    POST /user/tap/<UID>
    """
    uid = uid.upper().strip()
    if not uid:
        return jsonify({'error': 'UID is required'}), 400

    if not re.match(r'^[0-9A-F]{8}$', uid):
        return jsonify({'error': 'Invalid UID format. Expected 8 hex characters (e.g. 03142239)'}), 400

    _rfid_data['uid'] = uid
    try:
        record = insert_rfid_card(uid)
        _rfid_data['last_insert'] = record
        return jsonify({'success': True, 'record': record}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500