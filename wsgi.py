"""Production entry point:  gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 300 wsgi:app"""
from app import create_app

app = create_app()
