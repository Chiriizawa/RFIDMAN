from flask import Blueprint, jsonify, render_template
from datetime import datetime, date, time as datetime_time
import os
import re
import threading
import time
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
import serial

rfid_bp = Blueprint('rfid', __name__, template_folder='template')

# Simple in-memory storage
current_tap = {
    'uid': None,
    'student_name': None,
    'section_name': None,
    'schedule': None,
    'teacher_name': None,
    'status': None,
    'timestamp': None
}

last_processed_uid = None
_db_config = {}
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
    global current_tap
    current_tap = data

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
            password=p.password, sslmode='require',
            connect_timeout=3
        )
    return psycopg2.connect(**_db_config, connect_timeout=3)

def init_db():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Create attendance table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER REFERENCES students(id),
                    uid VARCHAR(50) NOT NULL,
                    attendance_date DATE NOT NULL,
                    time_in TIME,
                    time_out TIME,
                    status VARCHAR(20) DEFAULT 'present',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance(student_id, attendance_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_attendance_uid_date ON attendance(uid, attendance_date)")
        conn.commit()
        conn.close()
        print("[DB] Attendance table ready")
    except Exception as e:
        print(f"[DB] Error: {e}")

def format_time_12hr(time_obj):
    """Convert time to 12-hour format with AM/PM (no milliseconds)"""
    if not time_obj:
        return None
    return time_obj.strftime('%I:%M %p').lstrip('0')

def get_student_by_uid(uid):
    """Get student info from database"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT
                    s.id as student_id,
                    s.last_name,
                    s.first_name,
                    s.middle_name,
                    s.extension,
                    s.section_id,
                    sec.section_name,
                    t.last_name as teacher_last,
                    t.first_name as teacher_first
                FROM students s
                LEFT JOIN sections sec ON s.section_id = sec.id
                LEFT JOIN teachers t ON sec.teacher_id = t.id
                WHERE s.uid = %s
                LIMIT 1
            """, (uid,))
            student = cur.fetchone()
            
            if student:
                # Build full name
                full_name = f"{student['last_name']}, {student['first_name']}"
                if student['middle_name']:
                    full_name += f" {student['middle_name']}"
                if student['extension']:
                    full_name += f" {student['extension']}"
                
                # Build teacher name
                teacher_name = None
                if student['teacher_last'] and student['teacher_first']:
                    teacher_name = f"{student['teacher_last']}, {student['teacher_first']}"
                
                # Get schedule for this section from schedules table
                schedule_text = get_schedule_for_section(student['section_id'])
                
                return {
                    'student_id': student['student_id'],
                    'full_name': full_name,
                    'schedule': schedule_text,
                    'section_name': student['section_name'],
                    'teacher_name': teacher_name,
                    'section_id': student['section_id']
                }
            return None
    except Exception as e:
        print(f"Get student error: {e}")
        return None
    finally:
        conn.close()

def get_schedule_for_section(section_id):
    """Get formatted schedule for a section from schedules table"""
    if not section_id:
        return "No schedule available"
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT 
                    s.day,
                    s.time,
                    s.subject,
                    r.room_name,
                    t.first_name as teacher_first,
                    t.last_name as teacher_last
                FROM schedules s
                LEFT JOIN rooms r ON s.room_id = r.id
                LEFT JOIN teachers t ON s.teacher_id = t.id
                WHERE s.section_id = %s
                ORDER BY 
                    CASE s.day
                        WHEN 'Monday' THEN 1
                        WHEN 'Tuesday' THEN 2
                        WHEN 'Wednesday' THEN 3
                        WHEN 'Thursday' THEN 4
                        WHEN 'Friday' THEN 5
                        WHEN 'Saturday' THEN 6
                        WHEN 'Sunday' THEN 7
                    END,
                    s.time
            """, (section_id,))
            schedules = cur.fetchall()
            
            if not schedules:
                return "No schedule available"
            
            # Format schedule as readable text with AM/PM times
            schedule_lines = []
            current_day = None
            
            for sch in schedules:
                if sch['day'] != current_day:
                    current_day = sch['day']
                    schedule_lines.append(f"\n📅 {sch['day']}:")
                
                # Format time to 12-hour with AM/PM
                time_str = sch['time']
                formatted_time = "Time TBD"
                if time_str:
                    try:
                        # Handle string time like "07:00:00" or "07:00"
                        if isinstance(time_str, str):
                            if ':' in time_str:
                                parts = time_str.split(':')
                                hour = int(parts[0])
                                minute = parts[1]
                            else:
                                hour = int(time_str)
                                minute = "00"
                        else:
                            hour = time_str.hour
                            minute = time_str.minute
                        
                        ampm = 'AM' if hour < 12 else 'PM'
                        hour_display = hour if hour <= 12 else hour - 12
                        if hour_display == 0:
                            hour_display = 12
                        formatted_time = f"{hour_display}:{minute} {ampm}"
                    except:
                        formatted_time = str(time_str)
                
                subject = sch['subject'] or 'No Subject'
                room = sch['room_name'] or 'TBD'
                teacher = ""
                if sch['teacher_first'] and sch['teacher_last']:
                    teacher = f" ({sch['teacher_first']} {sch['teacher_last']})"
                
                schedule_lines.append(f"   • {formatted_time} - {subject} @ {room}{teacher}")
            
            return "\n".join(schedule_lines) if schedule_lines else "No schedule available"
            
    except Exception as e:
        print(f"Get schedule error: {e}")
        return "Error loading schedule"
    finally:
        conn.close()

def check_tapped_today(uid):
    """Check if student already tapped today"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            today_date = datetime.now().date()
            cur.execute("""
                SELECT id, time_in FROM attendance 
                WHERE attendance_date = %s AND uid = %s 
                LIMIT 1
            """, (today_date, uid))
            result = cur.fetchone()
            if result:
                time_in = result[1]
                formatted_time = format_time_12hr(time_in) if time_in else None
                return {'has_tapped': True, 'time_in': formatted_time}
            return {'has_tapped': False, 'time_in': None}
    except Exception as e:
        print(f"Check today error: {e}")
        return {'has_tapped': False, 'time_in': None}
    finally:
        conn.close()

def save_attendance(uid, student_id, student_name, section_name):
    """Save attendance to attendance table"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            today_date = datetime.now().date()
            current_time = datetime.now().time()
            
            # Check if already exists
            cur.execute("""
                SELECT id, time_in FROM attendance 
                WHERE attendance_date = %s AND uid = %s 
                LIMIT 1
            """, (today_date, uid))
            existing = cur.fetchone()
            
            if existing:
                # Already tapped today
                time_in = existing[1]
                formatted_time = format_time_12hr(time_in) if time_in else None
                print(f"[ATTENDANCE] Student {student_name} already tapped at {formatted_time}")
                return {'status': 'duplicate', 'time_in': formatted_time}
            else:
                # First tap of the day - insert new record
                cur.execute("""
                    INSERT INTO attendance (student_id, uid, attendance_date, time_in, status, created_at)
                    VALUES (%s, %s, %s, %s, 'present', NOW())
                    RETURNING id
                """, (student_id, uid, today_date, current_time))
                conn.commit()
                formatted_time = format_time_12hr(current_time)
                print(f"[ATTENDANCE] ✅ Saved: {student_name} at {formatted_time}")
                return {'status': 'success', 'time_in': formatted_time}
                
    except Exception as e:
        print(f"Save attendance error: {e}")
        return {'status': 'error', 'time_in': None}
    finally:
        conn.close()

def process_tap(uid):
    """Process a tap and update the global current_tap"""
    global current_tap, last_processed_uid
    
    # Prevent duplicate rapid processing
    if uid == last_processed_uid:
        return False
    
    # Get student info first
    student = get_student_by_uid(uid)
    
    if not student:
        # UID not found in database
        current_tap = {
            'uid': uid,
            'student_name': None,
            'section_name': None,
            'schedule': None,
            'teacher_name': None,
            'status': 'not_found',
            'timestamp': datetime.now().isoformat()
        }
        last_processed_uid = uid
        print(f"[RFID] ❌ UID {uid} not found in database")
        return False
    
    # Save to attendance table
    attendance_result = save_attendance(uid, student['student_id'], student['full_name'], student['section_name'])
    
    # Update global variable for display
    current_tap = {
        'uid': uid,
        'student_name': student['full_name'],
        'section_name': student['section_name'],
        'schedule': student['schedule'],
        'teacher_name': student['teacher_name'],
        'status': attendance_result['status'],
        'time_in': attendance_result['time_in'],
        'timestamp': datetime.now().isoformat()
    }
    
    last_processed_uid = uid
    print(f"[RFID] Processed: {uid} - {student['full_name']} - Status: {attendance_result['status']}")
    return True

def reader_thread():
    global _serial_port
    
    port = os.environ.get('RFID_PORT', 'COM3')
    print(f"[RFID] Listening on {port}...")
    
    while not _stop_event.is_set():
        try:
            _serial_port = serial.Serial(port, 9600, timeout=1)
            print(f"[RFID] ✅ Connected!")
            
            while not _stop_event.is_set():
                if _serial_port.in_waiting:
                    line = _serial_port.readline().decode().strip()
                    if line:
                        match = re.search(r'([0-9A-Fa-f]{8})', line)
                        if match:
                            uid = match.group(1).upper()
                            print(f"[RFID] 🎉 Card: {uid}")
                            process_tap(uid)
                time.sleep(0.05)
                
        except serial.SerialException as e:
            print(f"[RFID] Error: {e}")
            time.sleep(3)
        except Exception as e:
            print(f"[RFID] Error: {e}")
            time.sleep(3)
        finally:
            _close_serial()

def start_serial_reader():
    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()
    print("[RFID] Reader started")

# Routes
@rfid_bp.route('/')
def index():
    return render_template('user/index.html')

@rfid_bp.route('/status')
def status():
    """Return current tap data"""
    return jsonify({
        'uid': current_tap.get('uid', 'Waiting...'),
        'student_name': current_tap.get('student_name'),
        'section_name': current_tap.get('section_name'),
        'schedule': current_tap.get('schedule'),
        'teacher_name': current_tap.get('teacher_name'),
        'status': current_tap.get('status'),
        'time_in': current_tap.get('time_in'),
        'timestamp': current_tap.get('timestamp')
    })

@rfid_bp.route('/clear')
def clear():
    """Clear current tap data"""
    global current_tap
    current_tap = {
        'uid': 'Waiting...',
        'student_name': None,
        'section_name': None,
        'schedule': None,
        'teacher_name': None,
        'status': None,
        'time_in': None,
        'timestamp': None
    }
    print("[CLEAR] Status cleared")
    return jsonify({'success': True})

@rfid_bp.route('/tap_relay/<uid>', methods=['POST'])
def tap_relay(uid):
    """Manual tap endpoint"""
    uid = uid.upper().strip()
    if not re.match(r'^[0-9A-F]{8}$', uid):
        return jsonify({'error': 'Invalid UID'}), 400
    
    success = process_tap(uid)
    return jsonify({'success': success}), 200

@rfid_bp.route('/attendance/today')
def get_today_attendance():
    """Get today's attendance records"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            today_date = datetime.now().date()
            cur.execute("""
                SELECT 
                    a.id,
                    a.uid,
                    a.attendance_date,
                    a.time_in,
                    a.time_out,
                    a.status,
                    s.first_name,
                    s.last_name,
                    s.middle_name,
                    sec.section_name
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.id
                LEFT JOIN sections sec ON s.section_id = sec.id
                WHERE a.attendance_date = %s
                ORDER BY a.time_in DESC
            """, (today_date,))
            records = cur.fetchall()
            
            attendance_list = []
            for record in records:
                full_name = f"{record['last_name']}, {record['first_name']}"
                if record['middle_name']:
                    full_name += f" {record['middle_name']}"
                
                # Format time_in to AM/PM
                time_in = record['time_in']
                formatted_time = None
                if time_in:
                    formatted_time = time_in.strftime('%I:%M %p').lstrip('0')
                
                attendance_list.append({
                    'id': record['id'],
                    'uid': record['uid'],
                    'student_name': full_name,
                    'section_name': record['section_name'],
                    'time_in': formatted_time,
                    'status': record['status']
                })
            
            return jsonify({
                'success': True,
                'date': today_date.isoformat(),
                'count': len(attendance_list),
                'records': attendance_list
            })
    except Exception as e:
        print(f"Get today attendance error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

print("[RFID] Module loaded")