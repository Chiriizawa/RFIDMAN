from flask import Flask
from RFID.admin.admin import admin_bp


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'ray'

    app.register_blueprint(admin_bp, url_prefix='/admin')

    return app