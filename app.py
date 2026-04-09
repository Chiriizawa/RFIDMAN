# Add this temporarily in run.py to verify
from RFID import create_app

app = create_app()

if __name__ == '__main__':
    print(app.url_map)   # <-- will print all registered routes
    app.run(debug=True, use_reloader=False)