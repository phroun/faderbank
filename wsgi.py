#!/usr/bin/env python3
"""WSGI entry point for Zebby Faderbank with Gunicorn + eventlet."""

import sys
import os

# Add application directory to Python path
sys.path.insert(0, os.path.dirname(__file__))

# Import app and socketio for Gunicorn with eventlet worker
from app import app, socketio

# For Gunicorn: gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:5000 wsgi:app

if __name__ == '__main__':
    # Local development
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
