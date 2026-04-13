from flask import Flask
from RFID.admin.admin import admin_bp
from RFID.superadmin.sadmin import sadmin
from RFID.superadmin.sregister import sregister  

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'ray'

    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(sadmin, url_prefix='/superadmin')
    app.register_blueprint(sregister, url_prefix='/superadmin') 

    return app