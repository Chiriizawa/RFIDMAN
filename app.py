from RFID import create_app
from RFID.user.user import _close_serial, _stop_event

app = create_app()

if __name__ == "__main__":
    try:
        print(app.url_map)
        app.run(debug=True, host='0.0.0.0', port=5000)
    finally:
        # This ALWAYS runs when Flask stops
        print("[App] Cleaning up serial port...")
        _stop_event.set()
        _close_serial()