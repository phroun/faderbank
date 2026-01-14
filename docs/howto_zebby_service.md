# How to Set Up a Zebby Service

This guide explains how to create a new web service that integrates with Zebby for authentication.

## Overview

Zebby provides centralized WebAuthn authentication. Your service doesn't manage its own login - it validates the `zebby_session` cookie against the Zebby API.

## File Structure

```
your-service/
├── app.py                  # Flask application
├── database.py             # Database operations + auth helper
├── wsgi.py                 # WSGI entry point
├── schema.sql              # Database schema
├── config/
│   ├── db.py               # Database credentials (gitignored)
│   ├── db.py.orig          # Template for db.py
│   ├── zebby.py            # Zebby secrets (gitignored)
│   └── zebby.py.orig       # Template for zebby.py
├── templates/
│   └── index.html
├── static/
│   └── your-logo.svg
└── .gitignore
```

## Step 1: Config Files

### config/zebby.py.orig
```python
APP_SECRET_KEY = 'redacted'
FIRST_PARTY_SECRET = 'redacted'
```

### config/db.py.orig
```python
import pymysql
from pymysql.cursors import DictCursor

DB_CONFIG = {
    'host': 'localhost',
    'user': 'zebby_yourservice',
    'password': 'REDACTED',
    'database': 'zebby_yourservice',
    'charset': 'utf8mb4',
    'cursorclass': DictCursor
}

def get_db():
    return pymysql.connect(**DB_CONFIG)
```

### .gitignore
```
config/db.py
config/zebby.py
.DS_Store
__pycache__
venv/
```

## Step 2: Authentication Helper

In `database.py`, include this function:

```python
import requests
from flask import request

def get_zebby_user_info():
    """Get user info from Zebby API. Returns None if not logged in."""
    zebby_session = request.cookies.get('zebby_session')

    if not zebby_session:
        return None

    try:
        response = requests.get(
            'https://zebby.org/api/user/info',
            cookies={'zebby_session': zebby_session}
        )

        if response.status_code != 200:
            return None

        return response.json()
    except:
        return None
```

## Step 3: Flask App

### app.py
```python
from flask import Flask, render_template, redirect, request
import requests
import logging
from config.zebby import APP_SECRET_KEY
from db import get_zebby_user_info

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

# Configuration
API_BASE = 'https://zebby.org/api'
SERVICE_KEY = 'yourservice'  # Your service's unique key

def track_service_access():
    """Track that user accessed this service (optional)"""
    try:
        requests.post(
            f'{API_BASE}/services/touch',
            json={
                'service_key': SERVICE_KEY,
                'logo_url': f'https://zebby.org/{SERVICE_KEY}/static/logo.svg'
            },
            cookies=request.cookies,
            timeout=5
        )
    except Exception as e:
        logging.error(f"Service tracking error: {e}")

@app.route('/')
def index():
    user = get_zebby_user_info()

    if not user:
        return redirect(f'/login?return_to=/{SERVICE_KEY}/')

    track_service_access()

    return render_template('index.html', user=user)
```

## Step 4: User Data Available

When authenticated, `get_zebby_user_info()` returns:

```json
{
  "user_id": 1,
  "username": "jeffd1830",
  "display_name": "Jeff Day",
  "email": "user@example.com",
  "verified_email": "user@example.com",
  "created_at": "2025-10-05T10:38:25",
  "last_login_at": "2025-12-31T08:02:11",
  "seen_at": "2025-12-31T23:46:20",
  "client_seen_at": "2025-12-31T15:46:20",
  "timezone_offset": -480,
  "udate": "2025-12-31"
}
```

### Field Descriptions

| Field | Description |
|-------|-------------|
| `user_id` | Unique user identifier |
| `username` | Login username |
| `display_name` | User's preferred display name |
| `email` | Primary email address |
| `verified_email` | Email that has been verified |
| `created_at` | Account creation timestamp |
| `last_login_at` | Last WebAuthn login timestamp |
| `seen_at` | Last activity (server time) |
| `client_seen_at` | Last activity (client time) |
| `timezone_offset` | User's timezone offset in minutes |
| `udate` | Current date in user's timezone |

## Step 5: Login Flow

1. Check if user is authenticated: `user = get_zebby_user_info()`
2. If `user` is `None`, redirect to login:
   ```python
   return redirect('/login?return_to=/yourservice/')
   ```
3. Zebby handles the WebAuthn flow
4. User is redirected back to `return_to` URL with valid `zebby_session` cookie

### Important: Building the return_to URL

When using `WSGIScriptAlias` to mount your app at a subpath (e.g., `/yourservice`), Flask's `request.path` only returns the path *relative* to the mount point, not the full path.

For example, if your app is mounted at `/yourservice` and the user visits `/yourservice/dashboard`:
- `request.path` returns `/dashboard` (not `/yourservice/dashboard`)
- `request.url` returns the full URL but may cause issues if not URL-encoded

**Use `request.script_root + request.path`** to get the correct full path:

```python
from functools import wraps

def require_login(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_zebby_user_info()
        if not user:
            # request.script_root = '/yourservice' (the WSGIScriptAlias mount point)
            # request.path = '/dashboard' (path relative to mount point)
            # Combined = '/yourservice/dashboard'
            return_url = request.script_root + request.path
            return redirect(f'/login?return_to={return_url}')
        return f(user=user, *args, **kwargs)
    return decorated_function
```

| Method | Value at `/yourservice/dashboard` | Use Case |
|--------|-----------------------------------|----------|
| `request.path` | `/dashboard` | Within-app routing |
| `request.script_root` | `/yourservice` | The WSGI mount point |
| `request.script_root + request.path` | `/yourservice/dashboard` | Login return URLs |
| `request.url` | `https://zebby.org/yourservice/dashboard` | Full URL (needs encoding) |

## Step 6: Database Schema

Your service needs at minimum a `user` table to track users locally:

```sql
CREATE TABLE user (
    id INT PRIMARY KEY,           -- Matches Zebby user_id
    last_active_at DATETIME,
    -- Add your service-specific fields here
);
```

Optionally, a `session` table for storing session-specific data:

```sql
CREATE TABLE session (
    session_id VARCHAR(255) PRIMARY KEY,
    created_at DATETIME NOT NULL,
    last_accessed_at DATETIME NOT NULL,
    data JSON
);
```

## Optional: Guest User Support

If your service needs to allow limited functionality for users who haven't logged in (guests), use the `session` table for ephemeral storage not tied to a Zebby `user_id`.

### Pattern

1. Generate a unique session ID (e.g., UUID) and store it in a cookie
2. Store guest data in the `session` table keyed by this ID
3. When the user logs in, optionally migrate their guest session data to their authenticated user record
4. Clean up old guest sessions periodically (e.g., after 24 hours)

### Helper Functions

```python
import json
from datetime import datetime

class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return {'__datetime__': obj.isoformat()}
        return super().default(obj)

def datetime_decoder(dct):
    """Custom JSON decoder for datetime objects."""
    if '__datetime__' in dct:
        return datetime.fromisoformat(dct['__datetime__'])
    return dct

def save_session_data(session_id, data):
    """Save session data to database."""
    db = get_db()
    cursor = db.cursor()

    data_json = json.dumps(data, cls=DateTimeEncoder)

    cursor.execute(
        """INSERT INTO session (session_id, created_at, last_accessed_at, data)
           VALUES (%s, NOW(), NOW(), %s)
           ON DUPLICATE KEY UPDATE last_accessed_at = NOW(), data = VALUES(data)""",
        (session_id, data_json)
    )

    db.commit()
    cursor.close()
    db.close()

def get_session_data(session_id):
    """Load session data from database."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "SELECT data FROM session WHERE session_id = %s",
        (session_id,)
    )
    result = cursor.fetchone()

    if result:
        cursor.execute(
            "UPDATE session SET last_accessed_at = NOW() WHERE session_id = %s",
            (session_id,)
        )
        db.commit()
        cursor.close()
        db.close()

        data_str = result['data'] if isinstance(result, dict) else result[0]
        return json.loads(data_str, object_hook=datetime_decoder)

    cursor.close()
    db.close()
    return None

def cleanup_old_sessions():
    """Remove sessions older than 24 hours."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "DELETE FROM session WHERE last_accessed_at < DATE_SUB(NOW(), INTERVAL 24 HOUR)"
    )

    db.commit()
    cursor.close()
    db.close()
```

### Usage Example

```python
import uuid
from flask import make_response

@app.route('/guest-action')
def guest_action():
    user = get_zebby_user_info()

    if user:
        # Authenticated user - use their user_id
        # ...
    else:
        # Guest user - use ephemeral session
        session_id = request.cookies.get('guest_session')

        if not session_id:
            session_id = str(uuid.uuid4())

        data = get_session_data(session_id) or {'created': datetime.now()}
        data['last_action'] = 'some_action'
        save_session_data(session_id, data)

        response = make_response(render_template('guest.html'))
        response.set_cookie('guest_session', session_id, max_age=86400)
        return response
```

### When to Use

- Preview modes or trials before requiring login
- Shopping carts that persist before checkout
- Saving form progress for users who haven't signed up yet
- Any feature where forcing immediate login would hurt UX

### When NOT to Use

- Anything requiring trust or accountability
- Features where data loss would frustrate users (encourage login instead)
- Services where all users should be authenticated

## Step 7: Sync User to Local DB

When a user accesses your service, update your local user table:

```python
def get_zebby_user_info():
    # ... after getting user_data from API ...

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """INSERT INTO user (id, last_active_at)
           VALUES (%s, NOW())
           ON DUPLICATE KEY UPDATE last_active_at = NOW()""",
        (user_data['user_id'],)
    )

    db.commit()
    cursor.close()
    db.close()

    return user_data
```

## Step 8: WSGI Entry Point

### wsgi.py
```python
#!/usr/bin/env python3
import sys
import os

# Add application directory to Python path (required for mod_wsgi)
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application
```

**Note:** The variable must be named `application` for mod_wsgi compatibility. The `sys.path.insert` line is required because mod_wsgi doesn't automatically add your app directory to the Python path.

### Apache Configuration

```apache
WSGIDaemonProcess zebby_yourservice python-home=/var/www/zebby/yourservice/venv
WSGIProcessGroup zebby_yourservice
WSGIScriptAlias /yourservice /var/www/zebby/yourservice/wsgi.py

<Directory /var/www/zebby/yourservice>
    Require all granted
</Directory>
```

## Zebby API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/user/info` | GET | Get current user info (requires `zebby_session` cookie) |
| `/login` | GET | Redirect here with `?return_to=` param for login flow |
| `/api/services/touch` | POST | Track service access (optional) |

## Dependencies

```
flask
requests
pymysql
```

Install with:
```bash
python3 -m venv venv
source venv/bin/activate
pip install flask requests pymysql
```

## Key Points

1. **Don't manage your own sessions** - rely on `zebby_session` cookie
2. **Always validate via API** - call `/api/user/info` to check auth, don't trust local state
3. **Forward cookies** - when calling Zebby API, pass `cookies=request.cookies`
4. **Gitignore secrets** - never commit `config/db.py` or `config/zebby.py`
5. **Track service access** - optionally call `/api/services/touch` for usage analytics
