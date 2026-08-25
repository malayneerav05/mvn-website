import sys
import os

# Add your project directory to the sys.path
sys.path.insert(0, os.getcwd())

# Import the Flask app object from your app.py
from app import app as application
