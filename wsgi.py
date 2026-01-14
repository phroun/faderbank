#!/usr/bin/env python3
"""WSGI entry point for Zebby Faderbank."""

from app import app, socketio

if __name__ == '__main__':
    socketio.run(app)
