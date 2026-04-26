from flask import Flask
from dotenv import load_dotenv
import os
from urllib.parse import urlparse

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

from RFID.admin.admin import admin_bp
from RFID.superadmin.sadmin import sadmin
from RFID.superadmin.sregister import sregister
from RFID.superadmin.teacher import teacher_bp
from RFID.superadmin.section import section_bp
from RFID.user.user import rfid_bp, set_rfid_data, set_db_config, init_db, start_serial_reader


def create_app():
    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static"
    )
    app.config['SECRET_KEY'] = 'ray'

    # ── Database config ──────────────────────────
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL:
        parsed_url = urlparse(DATABASE_URL)
        db_config = {
            'DB_HOST':     parsed_url.hostname,
            'DB_PORT':     parsed_url.port,
            'DB_NAME':     parsed_url.path.lstrip('/'),
            'DB_USER':     parsed_url.username,
            'DB_PASSWORD': parsed_url.password,
        }
    else:
        raise ValueError("DATABASE_URL environment variable is not set")

    # ── Shared RFID state ────────────────────────
    rfid_data = {'uid': 'Waiting for RFID card...'}

    # ── Pass config & state to user blueprint ────
    set_rfid_data(rfid_data)
    set_db_config(db_config)

    # ── Register blueprints ──────────────────────
    app.register_blueprint(admin_bp,    url_prefix='/admin')
    app.register_blueprint(sadmin,      url_prefix='/superadmin')
    app.register_blueprint(sregister,   url_prefix='/superadmin')
    app.register_blueprint(teacher_bp,  url_prefix='/superadmin')
    app.register_blueprint(section_bp,  url_prefix='/superadmin')
    app.register_blueprint(rfid_bp,     url_prefix='/user')

    # ── Init DB table & start serial reader ──────
    init_db()
    start_serial_reader()

    return app