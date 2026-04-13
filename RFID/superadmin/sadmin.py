# sadmin.py
from flask import Blueprint, render_template

sadmin = Blueprint("sadmin", __name__, template_folder="template")

@sadmin.route('/')
def index():
    # Redirect to a placeholder or just show base layout
    return render_template('index.html', data={"message": "Welcome Admin"})