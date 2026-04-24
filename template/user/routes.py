import threading
import serial
from flask import Blueprint, jsonify, render_template

latest_rfid = {"uid": None}

def read_serial():
    try:
        ser = serial.Serial('COM3', 9600, timeout=1)
        print("[Serial] Listening on COM3...")
        while True:
            if ser.in_waiting:
                data = ser.readline().decode('utf-8').strip()
                if data:
                    latest_rfid["uid"] = data
                    print(f"[RFID] Detected: {data}")
    except serial.SerialException as e:
        print(f"[Serial ERROR] {e}")

threading.Thread(target=read_serial, daemon=True).start()

rfid_bp = Blueprint('rfid_bp', __name__, template_folder='.')

@rfid_bp.route('/')
def index():
    return render_template('index.html')

@rfid_bp.route('/rfid')
def get_rfid():
    return jsonify({"rfid": latest_rfid["uid"]})
