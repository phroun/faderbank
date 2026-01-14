"""Zebby Faderbank - Real-time collaborative fader bank application."""

from flask import Flask, render_template, redirect, request, jsonify, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from functools import wraps
import requests
import logging
import re
from datetime import datetime

from config.zebby import APP_SECRET_KEY
from database import (
    get_zebby_user_info, get_user_by_id,
    create_profile, get_profile_by_id, get_profile_by_slug, is_slug_available,
    update_profile, delete_profile, get_user_profiles,
    get_user_role, get_profile_members, add_profile_member, update_member_role,
    remove_profile_member, transfer_ownership,
    create_activation_link, get_activation_link, is_activation_link_valid,
    redeem_activation_link, cancel_activation_link, get_profile_activation_links,
    get_channel_strips, get_channel_strip, create_channel_strip, update_channel_strip,
    delete_channel_strip, reorder_channel_strips, update_fader_level,
    update_mute_state, update_solo_state,
    get_responsibility, take_responsibility, drop_responsibility
)

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")


@app.context_processor
def utility_processor():
    """Make utility functions available in templates."""
    return {'now': datetime.now}

# Configuration
API_BASE = 'https://zebby.org/api'
SERVICE_KEY = 'faderbank'

# Track online users per profile room
online_users = {}  # profile_id -> {user_id: {sid, user_info, ...}}


# =============================================================================
# Helper Functions
# =============================================================================

def track_service_access():
    """Track that user accessed this service."""
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


def require_login(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_zebby_user_info()
        if not user:
            return_url = request.script_root + request.path
            return redirect(f'/login?return_to={return_url}')
        return f(user=user, *args, **kwargs)
    return decorated_function


def require_profile_access(min_role=None):
    """Decorator to require profile access with optional minimum role."""
    def decorator(f):
        @wraps(f)
        def decorated_function(slug, *args, **kwargs):
            user = get_zebby_user_info()
            if not user:
                return_url = request.script_root + request.path
                return redirect(f'/login?return_to={return_url}')

            profile = get_profile_by_slug(slug)
            if not profile:
                return render_template('error.html', error="Profile not found"), 404

            role = get_user_role(profile['id'], user['user_id'])
            if not role:
                return render_template('error.html', error="You don't have access to this profile"), 403

            # Role hierarchy
            role_levels = {'owner': 5, 'admin': 4, 'technician': 3, 'operator': 2, 'guest': 1}

            if min_role and role_levels.get(role, 0) < role_levels.get(min_role, 0):
                return render_template('error.html', error="Insufficient permissions"), 403

            return f(user=user, profile=profile, role=role, *args, **kwargs)
        return decorated_function
    return decorator


def slugify(text):
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text


# =============================================================================
# Main Routes
# =============================================================================

@app.route('/debug')
def debug():
    """Debug route to check request values."""
    return jsonify({
        'script_root': request.script_root,
        'path': request.path,
        'full_path': request.full_path,
        'url': request.url,
        'combined': request.script_root + request.path
    })


@app.route('/')
def index():
    """Home page - show user's profiles or login prompt."""
    user = get_zebby_user_info()

    if not user:
        return_url = request.script_root + request.path
        return redirect(f'/login?return_to={return_url}')

    track_service_access()
    profiles = get_user_profiles(user['user_id'])

    return render_template('index.html', user=user, profiles=profiles)


@app.route('/profile/new', methods=['GET', 'POST'])
@require_login
def new_profile(user):
    """Create a new profile."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip()

        if not name or not slug:
            return render_template('profile_new.html', user=user,
                                   error="Name and slug are required")

        if not re.match(r'^[a-z0-9-]+$', slug):
            return render_template('profile_new.html', user=user,
                                   error="Slug must contain only lowercase letters, numbers, and hyphens")

        if not is_slug_available(slug):
            return render_template('profile_new.html', user=user,
                                   error="This slug is already taken")

        try:
            profile_id = create_profile(name, slug, user['user_id'])
            return redirect(f'/profile/{slug}')
        except Exception as e:
            return render_template('profile_new.html', user=user,
                                   error=str(e))

    return render_template('profile_new.html', user=user)


@app.route('/profile/<slug>')
@require_profile_access()
def view_profile(user, profile, role, slug):
    """Main profile view with canvas fader bank."""
    channels = get_channel_strips(profile['id'])
    responsibility = get_responsibility(profile['id'])
    members = get_profile_members(profile['id'])

    return render_template('profile_view.html',
                           user=user,
                           profile=profile,
                           role=role,
                           channels=channels,
                           responsibility=responsibility,
                           members=members)


@app.route('/profile/<slug>/config')
@require_profile_access(min_role='technician')
def profile_config(user, profile, role, slug):
    """Profile configuration page."""
    channels = get_channel_strips(profile['id'])
    members = get_profile_members(profile['id'])
    links = get_profile_activation_links(profile['id']) if role in ['owner', 'admin'] else []

    return render_template('profile_config.html',
                           user=user,
                           profile=profile,
                           role=role,
                           channels=channels,
                           members=members,
                           activation_links=links)


@app.route('/profile/<slug>/settings')
@require_profile_access(min_role='owner')
def profile_settings(user, profile, role, slug):
    """Profile settings (owner only)."""
    members = get_profile_members(profile['id'])

    return render_template('profile_settings.html',
                           user=user,
                           profile=profile,
                           role=role,
                           members=members)


# =============================================================================
# API Routes
# =============================================================================

@app.route('/api/slug/check')
@require_login
def check_slug(user):
    """Check if a slug is available."""
    slug = request.args.get('slug', '').strip().lower()
    exclude_id = request.args.get('exclude')

    if not slug:
        return jsonify({'available': False, 'error': 'Slug is required'})

    if not re.match(r'^[a-z0-9-]+$', slug):
        return jsonify({'available': False, 'error': 'Invalid characters'})

    available = is_slug_available(slug, exclude_id)
    return jsonify({'available': available})


@app.route('/api/profile/<int:profile_id>/update', methods=['POST'])
@require_login
def api_update_profile(user, profile_id):
    """Update profile details."""
    profile = get_profile_by_id(profile_id)
    if not profile:
        return jsonify({'error': 'Profile not found'}), 404

    role = get_user_role(profile_id, user['user_id'])
    if role not in ['owner', 'admin']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()
    name = data.get('name')
    slug = data.get('slug')

    if slug and slug != profile['slug']:
        if not is_slug_available(slug, profile_id):
            return jsonify({'error': 'Slug is already taken'}), 400

    update_profile(profile_id, name=name, slug=slug)
    return jsonify({'success': True})


@app.route('/api/profile/<int:profile_id>/delete', methods=['POST'])
@require_login
def api_delete_profile(user, profile_id):
    """Delete a profile (owner only)."""
    profile = get_profile_by_id(profile_id)
    if not profile:
        return jsonify({'error': 'Profile not found'}), 404

    if profile['owner_id'] != user['user_id']:
        return jsonify({'error': 'Only the owner can delete the profile'}), 403

    delete_profile(profile_id)
    return jsonify({'success': True})


@app.route('/api/profile/<int:profile_id>/transfer', methods=['POST'])
@require_login
def api_transfer_ownership(user, profile_id):
    """Transfer profile ownership."""
    profile = get_profile_by_id(profile_id)
    if not profile:
        return jsonify({'error': 'Profile not found'}), 404

    if profile['owner_id'] != user['user_id']:
        return jsonify({'error': 'Only the owner can transfer ownership'}), 403

    data = request.get_json()
    new_owner_id = data.get('new_owner_id')

    if not new_owner_id:
        return jsonify({'error': 'New owner ID is required'}), 400

    # Check new owner is a member
    new_owner_role = get_user_role(profile_id, new_owner_id)
    if not new_owner_role:
        return jsonify({'error': 'User is not a member of this profile'}), 400

    transfer_ownership(profile_id, new_owner_id)
    return jsonify({'success': True})


# =============================================================================
# Channel Strip API Routes
# =============================================================================

@app.route('/api/profile/<int:profile_id>/channel', methods=['POST'])
@require_login
def api_create_channel(user, profile_id):
    """Create a new channel strip."""
    role = get_user_role(profile_id, user['user_id'])
    if role not in ['owner', 'admin', 'technician']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()

    # Get current max position
    channels = get_channel_strips(profile_id)
    position = len(channels)

    channel_id = create_channel_strip(
        profile_id=profile_id,
        name=data.get('name', f'Channel {position + 1}'),
        position=position,
        color=data.get('color', 'white'),
        midi_cc_output=data.get('midi_cc_output', position),
        midi_cc_vu_input=data.get('midi_cc_vu_input'),
        midi_cc_mute=data.get('midi_cc_mute'),
        midi_cc_solo=data.get('midi_cc_solo'),
        min_level=data.get('min_level', 0),
        max_level=data.get('max_level', 127)
    )

    channel = get_channel_strip(channel_id)

    # Notify all users in the room
    socketio.emit('channel_added', {'channel': dict(channel)}, room=f'profile_{profile_id}')

    return jsonify({'success': True, 'channel_id': channel_id})


@app.route('/api/channel/<int:channel_id>/update', methods=['POST'])
@require_login
def api_update_channel(user, channel_id):
    """Update channel strip configuration."""
    channel = get_channel_strip(channel_id)
    if not channel:
        return jsonify({'error': 'Channel not found'}), 404

    role = get_user_role(channel['profile_id'], user['user_id'])
    if role not in ['owner', 'admin', 'technician']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()

    update_channel_strip(
        channel_id,
        name=data.get('name'),
        color=data.get('color'),
        midi_cc_output=data.get('midi_cc_output'),
        midi_cc_vu_input=data.get('midi_cc_vu_input'),
        midi_cc_mute=data.get('midi_cc_mute'),
        midi_cc_solo=data.get('midi_cc_solo'),
        min_level=data.get('min_level'),
        max_level=data.get('max_level')
    )

    updated_channel = get_channel_strip(channel_id)

    # Notify all users
    socketio.emit('channel_updated', {'channel': dict(updated_channel)},
                  room=f'profile_{channel["profile_id"]}')

    return jsonify({'success': True})


@app.route('/api/channel/<int:channel_id>/delete', methods=['POST'])
@require_login
def api_delete_channel(user, channel_id):
    """Delete a channel strip."""
    channel = get_channel_strip(channel_id)
    if not channel:
        return jsonify({'error': 'Channel not found'}), 404

    role = get_user_role(channel['profile_id'], user['user_id'])
    if role not in ['owner', 'admin', 'technician']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    profile_id = channel['profile_id']
    delete_channel_strip(channel_id)

    # Notify all users
    socketio.emit('channel_deleted', {'channel_id': channel_id}, room=f'profile_{profile_id}')

    return jsonify({'success': True})


@app.route('/api/profile/<int:profile_id>/channels/reorder', methods=['POST'])
@require_login
def api_reorder_channels(user, profile_id):
    """Reorder channel strips."""
    role = get_user_role(profile_id, user['user_id'])
    if role not in ['owner', 'admin', 'technician']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()
    channel_order = data.get('order', [])

    reorder_channel_strips(profile_id, channel_order)

    # Notify all users
    socketio.emit('channels_reordered', {'order': channel_order}, room=f'profile_{profile_id}')

    return jsonify({'success': True})


# =============================================================================
# Member Management API Routes
# =============================================================================

@app.route('/api/profile/<int:profile_id>/member/<int:member_user_id>/role', methods=['POST'])
@require_login
def api_update_member_role(user, profile_id, member_user_id):
    """Update a member's role."""
    profile = get_profile_by_id(profile_id)
    if not profile:
        return jsonify({'error': 'Profile not found'}), 404

    my_role = get_user_role(profile_id, user['user_id'])
    if my_role not in ['owner', 'admin']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    # Can't change owner's role
    if member_user_id == profile['owner_id']:
        return jsonify({'error': "Cannot change owner's role"}), 403

    # Admins can't change other admins
    target_role = get_user_role(profile_id, member_user_id)
    if my_role == 'admin' and target_role == 'admin':
        return jsonify({'error': "Admins cannot change other admin's roles"}), 403

    data = request.get_json()
    new_role = data.get('role')

    if new_role not in ['admin', 'technician', 'operator', 'guest']:
        return jsonify({'error': 'Invalid role'}), 400

    # Admins can't promote to admin
    if my_role == 'admin' and new_role == 'admin':
        return jsonify({'error': "Admins cannot promote to admin"}), 403

    update_member_role(profile_id, member_user_id, new_role)

    # Notify via WebSocket
    socketio.emit('member_updated', {
        'user_id': member_user_id,
        'role': new_role
    }, room=f'profile_{profile_id}')

    return jsonify({'success': True})


@app.route('/api/profile/<int:profile_id>/member/<int:member_user_id>/remove', methods=['POST'])
@require_login
def api_remove_member(user, profile_id, member_user_id):
    """Remove a member from profile."""
    profile = get_profile_by_id(profile_id)
    if not profile:
        return jsonify({'error': 'Profile not found'}), 404

    my_role = get_user_role(profile_id, user['user_id'])
    if my_role not in ['owner', 'admin']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    # Can't remove owner
    if member_user_id == profile['owner_id']:
        return jsonify({'error': "Cannot remove the owner"}), 403

    # Admins can't remove other admins
    target_role = get_user_role(profile_id, member_user_id)
    if my_role == 'admin' and target_role == 'admin':
        return jsonify({'error': "Admins cannot remove other admins"}), 403

    remove_profile_member(profile_id, member_user_id)

    # Notify via WebSocket
    socketio.emit('member_removed', {'user_id': member_user_id}, room=f'profile_{profile_id}')

    return jsonify({'success': True})


# =============================================================================
# Activation Link Routes
# =============================================================================

@app.route('/api/profile/<int:profile_id>/invite', methods=['POST'])
@require_login
def api_create_invite(user, profile_id):
    """Create an activation link."""
    role = get_user_role(profile_id, user['user_id'])
    if role not in ['owner', 'admin']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    data = request.get_json()
    invite_role = data.get('role', 'guest')

    if invite_role not in ['admin', 'technician', 'operator', 'guest']:
        return jsonify({'error': 'Invalid role'}), 400

    # Admins can't create admin invites
    if role == 'admin' and invite_role == 'admin':
        return jsonify({'error': "Admins cannot create admin invites"}), 403

    token = create_activation_link(profile_id, invite_role, user['user_id'])
    invite_url = url_for('redeem_invite', token=token, _external=True)

    return jsonify({'success': True, 'token': token, 'url': invite_url})


@app.route('/api/invite/<int:link_id>/cancel', methods=['POST'])
@require_login
def api_cancel_invite(user, link_id):
    """Cancel an activation link."""
    from database import get_db
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("SELECT * FROM activation_link WHERE id = %s", (link_id,))
        link = cursor.fetchone()
    finally:
        cursor.close()
        db.close()

    if not link:
        return jsonify({'error': 'Link not found'}), 404

    role = get_user_role(link['profile_id'], user['user_id'])
    if role not in ['owner', 'admin']:
        return jsonify({'error': 'Insufficient permissions'}), 403

    cancel_activation_link(link_id, link['profile_id'])
    return jsonify({'success': True})


@app.route('/invite/<token>')
def redeem_invite(token):
    """Redeem an activation link."""
    link = get_activation_link(token)

    if not link:
        return render_template('error.html', error="Invalid invitation link"), 404

    if not is_activation_link_valid(link):
        if link['used_at']:
            return render_template('error.html', error="This invitation has already been used")
        if link['canceled_at']:
            return render_template('error.html', error="This invitation has been canceled")
        return render_template('error.html', error="This invitation has expired")

    user = get_zebby_user_info()

    if not user:
        # Not logged in - show login prompt but don't consume the link
        return render_template('invite_login.html',
                               link=link,
                               return_url=request.url)

    # Check if already a member
    existing_role = get_user_role(link['profile_id'], user['user_id'])
    if existing_role:
        return render_template('invite_existing.html',
                               link=link,
                               role=existing_role,
                               user=user)

    # Redeem the link
    success, result = redeem_activation_link(token, user['user_id'])

    if success:
        return redirect(f'/profile/{result}')
    else:
        return render_template('error.html', error=result), 400


# =============================================================================
# WebSocket Events
# =============================================================================

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    pass


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection."""
    sid = request.sid

    # Remove user from all rooms they were in
    for profile_id, users in list(online_users.items()):
        for user_id, data in list(users.items()):
            if data.get('sid') == sid:
                del online_users[profile_id][user_id]

                # Notify room
                emit('user_left', {
                    'user_id': user_id
                }, room=f'profile_{profile_id}')

                # If they had responsibility, clear it
                resp = get_responsibility(profile_id)
                if resp and resp['user_id'] == user_id:
                    drop_responsibility(profile_id, user_id)
                    emit('responsibility_changed', {
                        'user_id': None,
                        'display_name': None
                    }, room=f'profile_{profile_id}')


@socketio.on('join_profile')
def handle_join_profile(data):
    """Join a profile room."""
    profile_id = data.get('profile_id')
    user_id = data.get('user_id')
    display_name = data.get('display_name')

    if not profile_id or not user_id:
        return

    room = f'profile_{profile_id}'
    join_room(room)

    # Track user
    if profile_id not in online_users:
        online_users[profile_id] = {}

    online_users[profile_id][user_id] = {
        'sid': request.sid,
        'display_name': display_name,
        'joined_at': datetime.now().isoformat()
    }

    # Send current online users to the joining user
    emit('online_users', {
        'users': {uid: {'display_name': d['display_name']} for uid, d in online_users[profile_id].items()}
    })

    # Notify room of new user
    emit('user_joined', {
        'user_id': user_id,
        'display_name': display_name
    }, room=room, include_self=False)

    # Send current state
    channels = get_channel_strips(profile_id)
    emit('channel_state', {'channels': [dict(c) for c in channels]})

    responsibility = get_responsibility(profile_id)
    if responsibility:
        emit('responsibility_changed', {
            'user_id': responsibility['user_id'],
            'display_name': responsibility['display_name']
        })


@socketio.on('leave_profile')
def handle_leave_profile(data):
    """Leave a profile room."""
    profile_id = data.get('profile_id')
    user_id = data.get('user_id')

    if not profile_id or not user_id:
        return

    room = f'profile_{profile_id}'
    leave_room(room)

    # Remove from tracking
    if profile_id in online_users and user_id in online_users[profile_id]:
        del online_users[profile_id][user_id]

    emit('user_left', {'user_id': user_id}, room=room)


@socketio.on('fader_change')
def handle_fader_change(data):
    """Handle fader level change."""
    channel_id = data.get('channel_id')
    level = data.get('level')
    user_id = data.get('user_id')
    is_final = data.get('is_final', False)

    if channel_id is None or level is None:
        return

    channel = get_channel_strip(channel_id)
    if not channel:
        return

    # Check permission
    role = get_user_role(channel['profile_id'], user_id)
    if role not in ['owner', 'admin', 'technician', 'operator']:
        return

    # Update database
    update_fader_level(channel_id, level)

    # Broadcast to room
    emit('fader_update', {
        'channel_id': channel_id,
        'level': level,
        'user_id': user_id,
        'is_final': is_final
    }, room=f'profile_{channel["profile_id"]}', include_self=False)


@socketio.on('mute_toggle')
def handle_mute_toggle(data):
    """Handle mute button toggle."""
    channel_id = data.get('channel_id')
    is_muted = data.get('is_muted')
    user_id = data.get('user_id')

    if channel_id is None or is_muted is None:
        return

    channel = get_channel_strip(channel_id)
    if not channel:
        return

    # Check permission
    role = get_user_role(channel['profile_id'], user_id)
    if role not in ['owner', 'admin', 'technician', 'operator']:
        return

    # Update database
    update_mute_state(channel_id, is_muted)

    # Broadcast to room
    emit('mute_update', {
        'channel_id': channel_id,
        'is_muted': is_muted,
        'user_id': user_id
    }, room=f'profile_{channel["profile_id"]}', include_self=False)


@socketio.on('solo_toggle')
def handle_solo_toggle(data):
    """Handle solo button toggle."""
    channel_id = data.get('channel_id')
    is_solo = data.get('is_solo')
    user_id = data.get('user_id')

    if channel_id is None or is_solo is None:
        return

    channel = get_channel_strip(channel_id)
    if not channel:
        return

    # Check permission
    role = get_user_role(channel['profile_id'], user_id)
    if role not in ['owner', 'admin', 'technician', 'operator']:
        return

    # Update database
    update_solo_state(channel_id, is_solo)

    # Broadcast to room
    emit('solo_update', {
        'channel_id': channel_id,
        'is_solo': is_solo,
        'user_id': user_id
    }, room=f'profile_{channel["profile_id"]}', include_self=False)


@socketio.on('vu_level')
def handle_vu_level(data):
    """Handle VU level update (from MIDI input)."""
    channel_id = data.get('channel_id')
    level = data.get('level')

    if channel_id is None or level is None:
        return

    channel = get_channel_strip(channel_id)
    if not channel:
        return

    # Broadcast to room (no permission check - VU is input only)
    emit('vu_update', {
        'channel_id': channel_id,
        'level': level
    }, room=f'profile_{channel["profile_id"]}', include_self=False)


@socketio.on('take_responsibility')
def handle_take_responsibility(data):
    """Handle taking responsibility."""
    profile_id = data.get('profile_id')
    user_id = data.get('user_id')
    display_name = data.get('display_name')
    force = data.get('force', False)

    if not profile_id or not user_id:
        return

    # Check permission
    role = get_user_role(profile_id, user_id)
    if role not in ['owner', 'admin', 'technician', 'operator']:
        return

    current = get_responsibility(profile_id)

    # If someone else has responsibility and not forcing, send confirmation request
    if current and current['user_id'] and current['user_id'] != user_id and not force:
        emit('confirm_take_responsibility', {
            'current_user_id': current['user_id'],
            'current_display_name': current['display_name']
        })
        return

    take_responsibility(profile_id, user_id)

    emit('responsibility_changed', {
        'user_id': user_id,
        'display_name': display_name
    }, room=f'profile_{profile_id}')


@socketio.on('drop_responsibility')
def handle_drop_responsibility(data):
    """Handle dropping responsibility."""
    profile_id = data.get('profile_id')
    user_id = data.get('user_id')

    if not profile_id or not user_id:
        return

    drop_responsibility(profile_id, user_id)

    emit('responsibility_changed', {
        'user_id': None,
        'display_name': None
    }, room=f'profile_{profile_id}')


# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error="Page not found"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error="Server error"), 500


if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
