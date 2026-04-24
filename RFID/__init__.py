from flask import Flask
from RFID.admin.admin import admin_bp
from RFID.superadmin.sadmin import sadmin
from RFID.superadmin.sregister import sregister
from RFID.superadmin.teacher import teacher_bp
from RFID.superadmin.section import section_bp
from RFID.user.user import rfid_bp, start_rfid_thread

def create_app():
    app = Flask(
        __name__,
        static_folder="../static",
        static_url_path="/static"
    )
    app.config['SECRET_KEY'] = 'ray'

    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(sadmin, url_prefix='/superadmin')
    app.register_blueprint(sregister, url_prefix='/superadmin')
    app.register_blueprint(rfid_bp, url_prefix='/user')
    app.register_blueprint(teacher_bp, url_prefix='/superadmin')
    app.register_blueprint(section_bp, url_prefix='/superadmin')
    start_rfid_thread()
    return app