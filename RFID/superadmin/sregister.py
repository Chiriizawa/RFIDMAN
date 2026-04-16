from flask import Blueprint, render_template, request, redirect, url_for, flash
import psycopg2
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

sregister = Blueprint("sregister", __name__, template_folder="template")


@sregister.route('/')
def student():
    return redirect(url_for('sregister.register'))