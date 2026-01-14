"""Database operations and authentication helpers for Zebby Faderbank."""

import requests
import secrets
import json
import logging
from datetime import datetime, timedelta
from flask import request
from config.db import get_db


# =============================================================================
# Zebby Authentication
# =============================================================================

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

        user_data = response.json()

        # Sync user to local database (non-fatal if it fails)
        try:
            sync_user(user_data)
        except Exception as e:
            logging.error(f"Failed to sync user to database: {e}")

        return user_data
    except:
        return None


def sync_user(user_data):
    """Sync Zebby user data to local database."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """INSERT INTO user (id, username, display_name, last_active_at, created_at)
               VALUES (%s, %s, %s, NOW(), NOW())
               ON DUPLICATE KEY UPDATE
                   username = VALUES(username),
                   display_name = VALUES(display_name),
                   last_active_at = NOW()""",
            (user_data['user_id'], user_data.get('username'), user_data.get('display_name'))
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def get_user_by_id(user_id):
    """Get user from local database."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("SELECT * FROM user WHERE id = %s", (user_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Profile Operations
# =============================================================================

def create_profile(name, slug, owner_id):
    """Create a new profile and add owner as member."""
    db = get_db()
    cursor = db.cursor()

    try:
        # Create profile
        cursor.execute(
            """INSERT INTO profile (name, slug, owner_id)
               VALUES (%s, %s, %s)""",
            (name, slug, owner_id)
        )
        profile_id = cursor.lastrowid

        # Add owner as member
        cursor.execute(
            """INSERT INTO profile_member (profile_id, user_id, role)
               VALUES (%s, %s, 'owner')""",
            (profile_id, owner_id)
        )

        # Initialize responsibility (no one has it initially)
        cursor.execute(
            """INSERT INTO profile_responsibility (profile_id, user_id, taken_at)
               VALUES (%s, NULL, NULL)""",
            (profile_id,)
        )

        db.commit()
        return profile_id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        cursor.close()
        db.close()


def get_profile_by_id(profile_id):
    """Get profile by ID."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("SELECT * FROM profile WHERE id = %s", (profile_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def get_profile_by_slug(slug):
    """Get profile by slug."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("SELECT * FROM profile WHERE slug = %s", (slug,))
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def is_slug_available(slug, exclude_profile_id=None):
    """Check if a slug is available."""
    db = get_db()
    cursor = db.cursor()

    try:
        if exclude_profile_id:
            cursor.execute(
                "SELECT id FROM profile WHERE slug = %s AND id != %s",
                (slug, exclude_profile_id)
            )
        else:
            cursor.execute("SELECT id FROM profile WHERE slug = %s", (slug,))
        return cursor.fetchone() is None
    finally:
        cursor.close()
        db.close()


def update_profile(profile_id, name=None, slug=None):
    """Update profile details."""
    db = get_db()
    cursor = db.cursor()

    try:
        updates = []
        params = []

        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if slug is not None:
            updates.append("slug = %s")
            params.append(slug)

        if updates:
            params.append(profile_id)
            cursor.execute(
                f"UPDATE profile SET {', '.join(updates)} WHERE id = %s",
                params
            )
            db.commit()
    finally:
        cursor.close()
        db.close()


def delete_profile(profile_id):
    """Delete a profile (cascades to members, channels, etc.)."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("DELETE FROM profile WHERE id = %s", (profile_id,))
        db.commit()
    finally:
        cursor.close()
        db.close()


def get_user_profiles(user_id):
    """Get all profiles a user has access to."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT p.*, pm.role
               FROM profile p
               JOIN profile_member pm ON p.id = pm.profile_id
               WHERE pm.user_id = %s
               ORDER BY p.name""",
            (user_id,)
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Profile Membership
# =============================================================================

def get_user_role(profile_id, user_id):
    """Get user's role in a profile. Returns None if not a member."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT role FROM profile_member
               WHERE profile_id = %s AND user_id = %s""",
            (profile_id, user_id)
        )
        result = cursor.fetchone()
        return result['role'] if result else None
    finally:
        cursor.close()
        db.close()


def get_profile_members(profile_id):
    """Get all members of a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT pm.*, u.username, u.display_name
               FROM profile_member pm
               JOIN user u ON pm.user_id = u.id
               WHERE pm.profile_id = %s
               ORDER BY FIELD(pm.role, 'owner', 'admin', 'technician', 'operator', 'guest'), u.display_name""",
            (profile_id,)
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


def add_profile_member(profile_id, user_id, role, added_by):
    """Add a user as a member of a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """INSERT INTO profile_member (profile_id, user_id, role, added_by)
               VALUES (%s, %s, %s, %s)""",
            (profile_id, user_id, role, added_by)
        )
        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        db.close()


def update_member_role(profile_id, user_id, new_role):
    """Update a member's role."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """UPDATE profile_member SET role = %s
               WHERE profile_id = %s AND user_id = %s""",
            (new_role, profile_id, user_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def remove_profile_member(profile_id, user_id):
    """Remove a user from a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """DELETE FROM profile_member
               WHERE profile_id = %s AND user_id = %s""",
            (profile_id, user_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def transfer_ownership(profile_id, new_owner_id):
    """Transfer profile ownership to another user."""
    db = get_db()
    cursor = db.cursor()

    try:
        # Get current owner
        cursor.execute("SELECT owner_id FROM profile WHERE id = %s", (profile_id,))
        profile = cursor.fetchone()
        old_owner_id = profile['owner_id']

        # Update profile owner
        cursor.execute(
            "UPDATE profile SET owner_id = %s WHERE id = %s",
            (new_owner_id, profile_id)
        )

        # Update roles
        cursor.execute(
            """UPDATE profile_member SET role = 'admin'
               WHERE profile_id = %s AND user_id = %s""",
            (profile_id, old_owner_id)
        )
        cursor.execute(
            """UPDATE profile_member SET role = 'owner'
               WHERE profile_id = %s AND user_id = %s""",
            (profile_id, new_owner_id)
        )

        db.commit()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Activation Links
# =============================================================================

def create_activation_link(profile_id, role, created_by):
    """Create a single-use activation link."""
    db = get_db()
    cursor = db.cursor()

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=7)

    try:
        cursor.execute(
            """INSERT INTO activation_link (profile_id, token, role, created_by, expires_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (profile_id, token, role, created_by, expires_at)
        )
        db.commit()
        return token
    finally:
        cursor.close()
        db.close()


def get_activation_link(token):
    """Get activation link by token."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT al.*, p.name as profile_name, p.slug as profile_slug
               FROM activation_link al
               JOIN profile p ON al.profile_id = p.id
               WHERE al.token = %s""",
            (token,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def is_activation_link_valid(link):
    """Check if an activation link is still valid."""
    if not link:
        return False
    if link['used_at'] is not None:
        return False
    if link['canceled_at'] is not None:
        return False
    if link['expires_at'] < datetime.now():
        return False
    return True


def redeem_activation_link(token, user_id):
    """Redeem an activation link for a user."""
    db = get_db()
    cursor = db.cursor()

    try:
        # Get the link
        link = get_activation_link(token)

        if not is_activation_link_valid(link):
            return False, "Link is no longer valid"

        # Check if user is already a member
        existing_role = get_user_role(link['profile_id'], user_id)
        if existing_role:
            return False, "You already have access to this profile"

        # Add user as member
        cursor.execute(
            """INSERT INTO profile_member (profile_id, user_id, role, added_by)
               VALUES (%s, %s, %s, %s)""",
            (link['profile_id'], user_id, link['role'], link['created_by'])
        )

        # Mark link as used
        cursor.execute(
            """UPDATE activation_link SET used_by = %s, used_at = NOW()
               WHERE token = %s""",
            (user_id, token)
        )

        db.commit()
        return True, link['profile_slug']
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        cursor.close()
        db.close()


def cancel_activation_link(link_id, profile_id):
    """Cancel an activation link."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """UPDATE activation_link SET canceled_at = NOW()
               WHERE id = %s AND profile_id = %s""",
            (link_id, profile_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def get_profile_activation_links(profile_id):
    """Get all activation links for a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT al.*,
                      creator.display_name as creator_name,
                      redeemer.display_name as redeemer_name
               FROM activation_link al
               JOIN user creator ON al.created_by = creator.id
               LEFT JOIN user redeemer ON al.used_by = redeemer.id
               WHERE al.profile_id = %s
               ORDER BY al.created_at DESC""",
            (profile_id,)
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Channel Strip Operations
# =============================================================================

def get_channel_strips(profile_id):
    """Get all channel strips for a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT * FROM channel_strip
               WHERE profile_id = %s
               ORDER BY position""",
            (profile_id,)
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


def get_channel_strip(channel_id):
    """Get a single channel strip."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("SELECT * FROM channel_strip WHERE id = %s", (channel_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def create_channel_strip(profile_id, name, position, color='white',
                         midi_cc_output=0, midi_cc_vu_input=None,
                         midi_cc_mute=None, midi_cc_solo=None,
                         min_level=0, max_level=127):
    """Create a new channel strip."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """INSERT INTO channel_strip
               (profile_id, name, position, color, midi_cc_output, midi_cc_vu_input,
                midi_cc_mute, midi_cc_solo, min_level, max_level)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (profile_id, name, position, color, midi_cc_output, midi_cc_vu_input,
             midi_cc_mute, midi_cc_solo, min_level, max_level)
        )
        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        db.close()


def update_channel_strip(channel_id, **kwargs):
    """Update channel strip properties."""
    if not kwargs:
        return

    db = get_db()
    cursor = db.cursor()

    allowed_fields = ['name', 'position', 'color', 'midi_cc_output', 'midi_cc_vu_input',
                      'midi_cc_mute', 'midi_cc_solo', 'min_level', 'max_level',
                      'current_level', 'is_muted', 'is_solo']

    updates = []
    params = []

    for field, value in kwargs.items():
        if field in allowed_fields:
            updates.append(f"{field} = %s")
            params.append(value)

    if updates:
        params.append(channel_id)
        try:
            cursor.execute(
                f"UPDATE channel_strip SET {', '.join(updates)} WHERE id = %s",
                params
            )
            db.commit()
        finally:
            cursor.close()
            db.close()


def delete_channel_strip(channel_id):
    """Delete a channel strip."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("DELETE FROM channel_strip WHERE id = %s", (channel_id,))
        db.commit()
    finally:
        cursor.close()
        db.close()


def reorder_channel_strips(profile_id, channel_order):
    """Reorder channel strips. channel_order is a list of channel IDs in desired order."""
    db = get_db()
    cursor = db.cursor()

    try:
        for position, channel_id in enumerate(channel_order):
            cursor.execute(
                """UPDATE channel_strip SET position = %s
                   WHERE id = %s AND profile_id = %s""",
                (position, channel_id, profile_id)
            )
        db.commit()
    finally:
        cursor.close()
        db.close()


def update_fader_level(channel_id, level):
    """Update just the fader level (optimized for frequent updates)."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "UPDATE channel_strip SET current_level = %s, state_version = state_version + 1 WHERE id = %s",
            (level, channel_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def update_mute_state(channel_id, is_muted):
    """Update mute state."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "UPDATE channel_strip SET is_muted = %s, state_version = state_version + 1 WHERE id = %s",
            (is_muted, channel_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def update_solo_state(channel_id, is_solo):
    """Update solo state."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "UPDATE channel_strip SET is_solo = %s, state_version = state_version + 1 WHERE id = %s",
            (is_solo, channel_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def update_vu_level(channel_id, level):
    """Update VU meter level (no version increment - ephemeral data)."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "UPDATE channel_strip SET vu_level = %s WHERE id = %s",
            (level, channel_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def update_vu_levels_bulk(profile_id, vu_data):
    """Update multiple VU levels at once. vu_data is {channel_id: level}."""
    db = get_db()
    cursor = db.cursor()

    try:
        for channel_id, level in vu_data.items():
            cursor.execute(
                "UPDATE channel_strip SET vu_level = %s WHERE id = %s AND profile_id = %s",
                (level, channel_id, profile_id)
            )
        db.commit()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Responsibility System
# =============================================================================

def get_responsibility(profile_id):
    """Get who has responsibility for a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT pr.*, u.username, u.display_name
               FROM profile_responsibility pr
               LEFT JOIN user u ON pr.user_id = u.id
               WHERE pr.profile_id = %s""",
            (profile_id,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def take_responsibility(profile_id, user_id):
    """Take responsibility for a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """UPDATE profile_responsibility
               SET user_id = %s, taken_at = NOW()
               WHERE profile_id = %s""",
            (user_id, profile_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def drop_responsibility(profile_id, user_id):
    """Drop responsibility (only if you have it)."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """UPDATE profile_responsibility
               SET user_id = NULL, taken_at = NULL
               WHERE profile_id = %s AND user_id = %s""",
            (profile_id, user_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Session Helpers (for ephemeral data)
# =============================================================================

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

    try:
        cursor.execute(
            """INSERT INTO session (session_id, created_at, last_accessed_at, data)
               VALUES (%s, NOW(), NOW(), %s)
               ON DUPLICATE KEY UPDATE last_accessed_at = NOW(), data = VALUES(data)""",
            (session_id, data_json)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def get_session_data(session_id):
    """Load session data from database."""
    db = get_db()
    cursor = db.cursor()

    try:
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

            data_str = result['data'] if isinstance(result, dict) else result[0]
            return json.loads(data_str, object_hook=datetime_decoder)

        return None
    finally:
        cursor.close()
        db.close()


def cleanup_old_sessions():
    """Remove sessions older than 24 hours."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "DELETE FROM session WHERE last_accessed_at < DATE_SUB(NOW(), INTERVAL 24 HOUR)"
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


# =============================================================================
# Profile Activity Tracking (for online users list)
# =============================================================================

def update_profile_activity(profile_id, user_id):
    """Update user's last seen time for a profile."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """INSERT INTO profile_activity (profile_id, user_id, last_seen_at)
               VALUES (%s, %s, NOW())
               ON DUPLICATE KEY UPDATE last_seen_at = NOW()""",
            (profile_id, user_id)
        )
        db.commit()
    finally:
        cursor.close()
        db.close()


def get_active_users(profile_id, timeout_seconds=30):
    """Get users who have been active in the last N seconds."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            """SELECT pa.user_id, u.username, u.display_name
               FROM profile_activity pa
               JOIN user u ON pa.user_id = u.id
               WHERE pa.profile_id = %s
                 AND pa.last_seen_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
               ORDER BY pa.last_seen_at DESC""",
            (profile_id, timeout_seconds)
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


def cleanup_old_activity():
    """Remove activity records older than 5 minutes."""
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "DELETE FROM profile_activity WHERE last_seen_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE)"
        )
        db.commit()
    finally:
        cursor.close()
        db.close()
