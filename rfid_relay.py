import serial
import requests
import time
import re

# Your Arduino COM port (find it in Device Manager)
ARDUINO_PORT = "COM3"  # Change to your Arduino port
BAUD_RATE = 9600
FLASK_URL = "http://127.0.0.1:5000/user/tap_relay"  # Endpoint to receive taps

print(f"Connecting to Arduino on {ARDUINO_PORT}...")

try:
    ser = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=1)
    print("✅ Connected to Arduino!")
    print("Waiting for RFID taps...")
    
    last_uid = ""
    
    while True:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                # Look for UID (8 hex characters)
                match = re.search(r'([0-9A-Fa-f]{8})', line)
                if match:
                    uid = match.group(1).upper()
                    
                    # Avoid duplicates
                    if uid != last_uid:
                        last_uid = uid
                        print(f"📡 RFID detected: {uid}")
                        
                        # Send to Flask app
                        try:
                            response = requests.post(
                                f"{FLASK_URL}/{uid}",
                                timeout=1
                            )
                            if response.status_code == 200:
                                print(f"✅ Sent to web: {uid}")
                            else:
                                print(f"❌ Failed to send: {response.status_code}")
                        except Exception as e:
                            print(f"❌ Connection error: {e}")
        
        time.sleep(0.05)
        
except serial.SerialException as e:
    print(f"❌ Cannot open {ARDUINO_PORT}: {e}")
    print("Make sure:")
    print("1. Arduino is connected via USB")
    print("2. No other program is using the port")
except KeyboardInterrupt:
    print("\nStopping...")
    ser.close()