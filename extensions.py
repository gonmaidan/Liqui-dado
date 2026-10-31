"""
extensions.py
---------------
Instancias compartidas (SQLAlchemy, Flask-Login) en un módulo aparte para
evitar imports circulares entre app.py, models.py y auth.py.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Iniciá sesión para continuar."
login_manager.login_message_category = "info"
