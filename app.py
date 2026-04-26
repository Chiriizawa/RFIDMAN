from RFID import create_app
from RFID.user.user import _close_serial, _stop_event

app = create_app()

if __name__ == "__main__":
    try:
        print(app.url_map)
        app.run(debug=False, use_reloader=False)  # debug=False is important
    finally:
        # This ALWAYS runs when Flask stops — even on force kill in VSCode
        print("[App] Cleaning up serial port...")
        _stop_event.set()
        _close_serial()