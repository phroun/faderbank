#!/usr/bin/env python3
"""WSGI entry point for Zebby Faderbank."""

import sys
import os

# Add application directory to Python path
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application

# For local development with WebSocket support:
# from app import app, socketio
# if __name__ == '__main__':
#     socketio.run(app, debug=True, host='0.0.0.0', port=5000)
