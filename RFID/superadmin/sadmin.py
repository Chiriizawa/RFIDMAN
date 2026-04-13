# sadmin.py
from flask import Blueprint, render_template, ect # pyright: ignore[reportMissingImports]redir

sadmin = Blueprint('sadmin', __name__, url_prefix='/sadmin')

@sadmin.route('/')
def index():
    # Redirect to a placeholder or just show base layout
    return render_template('index.html')