/**
 * Zebby Faderbank - Canvas-based fader bank UI
 */

(function() {
    'use strict';

    // Configuration from server
    const config = window.FADERBANK;

    // Constants
    const STRIP_WIDTH = 80;
    const STRIP_PADDING = 10;
    const FADER_WIDTH = 40;
    const FADER_HEIGHT = 200;
    const FADER_TOP = 80;
    const VU_WIDTH = 8;
    const VU_HEIGHT = 180;
    const BUTTON_SIZE = 30;
    const BUTTON_GAP = 5;

    // Colors
    const COLORS = {
        red: '#ef4444',
        orange: '#f97316',
        yellow: '#eab308',
        green: '#22c55e',
        cyan: '#06b6d4',
        blue: '#3b82f6',
        purple: '#a855f7',
        white: '#e5e5e5'
    };

    // State
    let channels = [...config.channels];
    let onlineUsers = config.initialOnlineUsers || {};
    let responsibilityUser = config.initialResponsibility || null;
    let socket = null;
    let canvas = null;
    let ctx = null;

    // Version tracking for each channel (prevents stale updates)
    const channelVersions = {};
    channels.forEach(ch => {
        channelVersions[ch.id] = ch.state_version || 0;
    });

    // Interaction state
    let activeFader = null;
    let lastSendTime = 0;
    let pendingFaderValue = null;

    // MIDI state
    let midiEnabled = false;
    let midiOutput = null;
    let midiInput = null;
    let midiChannel = 1;
    let midiAccess = null;

    // VU peak tracking
    const vuPeaks = {};  // channel_id -> {peak, lastUpdate}
    const VU_PEAK_INTERVAL = 100;  // 100ms

    // Polling state
    const POLL_INTERVAL = 500;  // Poll every 500ms
    let pollTimer = null;
    let isPolling = false;

    // ==========================================================================
    // Initialization
    // ==========================================================================

    function init() {
        canvas = document.getElementById('faderbank-canvas');
        ctx = canvas.getContext('2d');

        setupCanvas();
        setupEventListeners();
        setupSocket();
        setupMidiControls();
        setupResponsibilityControls();
        loadMidiSettings();
        updateOnlineUsersList();
        startPolling();

        render();

        // Start animation loop
        requestAnimationFrame(animationLoop);
    }

    // ==========================================================================
    // State Polling (for mod_wsgi compatibility)
    // ==========================================================================

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(pollState, POLL_INTERVAL);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    async function pollState() {
        if (isPolling) return;  // Skip if previous poll still in progress
        isPolling = true;

        try {
            const response = await fetch(`${window.BASE_URL}/api/profile/${config.profileId}/state`);
            if (!response.ok) return;

            const data = await response.json();
            applyStateUpdate(data);
        } catch (err) {
            // Silently ignore polling errors
        } finally {
            isPolling = false;
        }
    }

    function applyStateUpdate(data) {
        let needsRender = false;
        let needsMidiRecalc = false;

        // Update channel states
        if (data.channels) {
            for (const update of data.channels) {
                const channel = channels.find(c => c.id === update.id);
                if (!channel) continue;

                const serverVersion = update.version || 0;
                const localVersion = channelVersions[channel.id] || 0;

                // Only apply if server has newer version
                if (serverVersion <= localVersion) continue;

                // Update our tracked version
                channelVersions[channel.id] = serverVersion;

                // Only update fader if we're not currently dragging it
                if (activeFader !== channel) {
                    if (channel.current_level !== update.current_level) {
                        channel.current_level = update.current_level;
                        needsRender = true;
                        sendMidiFader(channel);
                    }
                }

                if (channel.is_muted !== update.is_muted) {
                    channel.is_muted = update.is_muted;
                    needsRender = true;
                    needsMidiRecalc = true;
                    sendMidiMute(channel);
                }

                if (channel.is_solo !== update.is_solo) {
                    channel.is_solo = update.is_solo;
                    needsRender = true;
                    needsMidiRecalc = true;
                    sendMidiSolo(channel);
                }
            }
        }

        // Update responsibility
        if (data.responsibility !== undefined) {
            const oldUser = responsibilityUser;
            responsibilityUser = data.responsibility;

            if (JSON.stringify(oldUser) !== JSON.stringify(responsibilityUser)) {
                updateResponsibilityUI();
                needsRender = true;
            }
        }

        // Update online users from polling
        if (data.online_users !== undefined) {
            onlineUsers = data.online_users;
            updateOnlineUsersList();
        }

        if (needsMidiRecalc) {
            recalculateMidiOutputs();
        }

        if (needsRender) {
            render();
        }
    }

    function setupCanvas() {
        const container = canvas.parentElement;
        const rect = container.getBoundingClientRect();

        // Calculate required width
        const minWidth = channels.length * (STRIP_WIDTH + STRIP_PADDING) + STRIP_PADDING;
        const width = Math.max(rect.width, minWidth);
        const height = rect.height;

        // Set canvas size with device pixel ratio for sharp rendering
        const dpr = window.devicePixelRatio || 1;
        canvas.width = width * dpr;
        canvas.height = height * dpr;
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        ctx.scale(dpr, dpr);

        // Store dimensions for calculations
        canvas.displayWidth = width;
        canvas.displayHeight = height;
    }

    // ==========================================================================
    // WebSocket
    // ==========================================================================

    function setupSocket() {
        // Using polling transport for mod_wsgi compatibility
        // Remove {transports: ['polling']} to enable WebSocket when using gunicorn+eventlet
        socket = io({
            path: window.BASE_URL + '/socket.io',
            transports: ['polling']
        });

        socket.on('connect', () => {
            socket.emit('join_profile', {
                profile_id: config.profileId,
                user_id: config.userId,
                display_name: config.displayName
            });
        });

        socket.on('disconnect', () => {
            console.log('Disconnected from server');
        });

        socket.on('channel_state', (data) => {
            channels = data.channels;
            render();
        });

        socket.on('online_users', (data) => {
            onlineUsers = data.users;
            updateOnlineUsersList();
        });

        socket.on('user_joined', (data) => {
            onlineUsers[data.user_id] = { display_name: data.display_name };
            updateOnlineUsersList();
        });

        socket.on('user_left', (data) => {
            delete onlineUsers[data.user_id];
            updateOnlineUsersList();
        });

        socket.on('fader_update', (data) => {
            const channel = channels.find(c => c.id === data.channel_id);
            if (channel) {
                channel.current_level = data.level;
                render();
                sendMidiFader(channel);
            }
        });

        socket.on('mute_update', (data) => {
            const channel = channels.find(c => c.id === data.channel_id);
            if (channel) {
                channel.is_muted = data.is_muted;
                render();
                sendMidiMute(channel);
                recalculateMidiOutputs();
            }
        });

        socket.on('solo_update', (data) => {
            const channel = channels.find(c => c.id === data.channel_id);
            if (channel) {
                channel.is_solo = data.is_solo;
                render();
                sendMidiSolo(channel);
                recalculateMidiOutputs();
            }
        });

        socket.on('vu_update', (data) => {
            const channel = channels.find(c => c.id === data.channel_id);
            if (channel) {
                channel.vu_level = data.level;
                // Don't call render() here - animation loop handles it
            }
        });

        socket.on('channel_added', (data) => {
            channels.push(data.channel);
            setupCanvas();
            render();
        });

        socket.on('channel_updated', (data) => {
            const index = channels.findIndex(c => c.id === data.channel.id);
            if (index !== -1) {
                channels[index] = data.channel;
                render();
            }
        });

        socket.on('channel_deleted', (data) => {
            channels = channels.filter(c => c.id !== data.channel_id);
            setupCanvas();
            render();
        });

        socket.on('channels_reordered', (data) => {
            const newChannels = [];
            data.order.forEach(id => {
                const channel = channels.find(c => c.id === id);
                if (channel) newChannels.push(channel);
            });
            channels = newChannels;
            render();
        });

        socket.on('responsibility_changed', (data) => {
            responsibilityUser = data.user_id ? {
                user_id: data.user_id,
                display_name: data.display_name
            } : null;
            updateResponsibilityUI();
            updateOnlineUsersList();
        });

        socket.on('confirm_take_responsibility', (data) => {
            showResponsibilityConfirmModal(data.current_display_name);
        });

        socket.on('member_updated', (data) => {
            // Refresh if our role changed
            if (data.user_id === config.userId) {
                location.reload();
            }
        });

        socket.on('member_removed', (data) => {
            if (data.user_id === config.userId) {
                location.href = '/';
            }
        });
    }

    // ==========================================================================
    // Canvas Rendering
    // ==========================================================================

    function render() {
        const width = canvas.displayWidth;
        const height = canvas.displayHeight;

        // Clear canvas
        ctx.fillStyle = '#16213e';
        ctx.fillRect(0, 0, width, height);

        // Check if any solo is active
        const anySolo = channels.some(c => c.is_solo);

        // Render each channel strip
        channels.forEach((channel, index) => {
            renderChannelStrip(channel, index, anySolo);
        });
    }

    function renderChannelStrip(channel, index, anySolo) {
        const x = STRIP_PADDING + index * (STRIP_WIDTH + STRIP_PADDING);
        const color = COLORS[channel.color] || COLORS.white;

        // Determine if channel is effectively muted (muted or not solo'd when solo active)
        const isEffectivelyMuted = channel.is_muted || (anySolo && !channel.is_solo);

        // Strip background
        ctx.fillStyle = isEffectivelyMuted ? 'rgba(30, 30, 50, 0.8)' : 'rgba(30, 30, 50, 0.5)';
        ctx.fillRect(x, 0, STRIP_WIDTH, canvas.displayHeight);

        // Channel name
        ctx.fillStyle = isEffectivelyMuted ? '#666' : '#eee';
        ctx.font = 'bold 12px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(truncateText(channel.name, STRIP_WIDTH - 10), x + STRIP_WIDTH / 2, 25);

        // Color indicator bar
        ctx.fillStyle = isEffectivelyMuted ? '#444' : color;
        ctx.fillRect(x + 10, 35, STRIP_WIDTH - 20, 4);

        // VU meter (left side of fader)
        const vuX = x + 10;
        renderVUMeter(channel, vuX, FADER_TOP, VU_WIDTH, VU_HEIGHT, isEffectivelyMuted);

        // Fader track
        const faderX = x + (STRIP_WIDTH - FADER_WIDTH) / 2;
        renderFaderTrack(channel, faderX, FADER_TOP, FADER_WIDTH, FADER_HEIGHT, isEffectivelyMuted, color);

        // Mute button
        const buttonY = FADER_TOP + FADER_HEIGHT + 20;
        renderButton(x + 10, buttonY, BUTTON_SIZE, 'M', channel.is_muted, '#ef4444');

        // Solo button
        renderButton(x + STRIP_WIDTH - 10 - BUTTON_SIZE, buttonY, BUTTON_SIZE, 'S', channel.is_solo, '#eab308');

        // Level value
        ctx.fillStyle = '#888';
        ctx.font = '11px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(channel.current_level.toString(), x + STRIP_WIDTH / 2, buttonY + BUTTON_SIZE + 20);
    }

    function renderVUMeter(channel, x, y, width, height, isMuted) {
        // Background
        ctx.fillStyle = '#0a0a15';
        ctx.fillRect(x, y, width, height);

        if (channel.vu_level !== undefined && channel.vu_level > 0) {
            const level = channel.vu_level / 127;
            const meterHeight = height * level;

            // Save context for alpha manipulation
            ctx.save();

            // Dim the VU meter if muted (30% opacity vs full)
            if (isMuted) {
                ctx.globalAlpha = 0.3;
            }

            // Gradient from green to yellow to red
            const gradient = ctx.createLinearGradient(x, y + height, x, y);
            gradient.addColorStop(0, '#22c55e');
            gradient.addColorStop(0.6, '#22c55e');
            gradient.addColorStop(0.8, '#eab308');
            gradient.addColorStop(1, '#ef4444');

            ctx.fillStyle = gradient;
            ctx.fillRect(x, y + height - meterHeight, width, meterHeight);

            // Restore context
            ctx.restore();
        }

        // Border
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, width, height);
    }

    function renderFaderTrack(channel, x, y, width, height, isMuted, color) {
        // Track background
        ctx.fillStyle = '#0a0a15';
        ctx.fillRect(x, y, width, height);

        // Track groove
        const grooveX = x + width / 2 - 2;
        ctx.fillStyle = '#222';
        ctx.fillRect(grooveX, y + 5, 4, height - 10);

        // Calculate fader position
        const level = channel.current_level;
        const minLevel = channel.min_level || 0;
        const maxLevel = channel.max_level || 127;
        const range = maxLevel - minLevel;
        const normalizedLevel = range > 0 ? (level - minLevel) / range : 0;
        const faderY = y + height - 30 - (normalizedLevel * (height - 40));

        // Fader cap (the part you grab)
        const capHeight = 30;
        const capWidth = width - 10;
        const capX = x + 5;

        // Fader cap shadow
        ctx.fillStyle = 'rgba(0,0,0,0.3)';
        ctx.fillRect(capX + 2, faderY + 2, capWidth, capHeight);

        // Fader cap body
        const capGradient = ctx.createLinearGradient(capX, 0, capX + capWidth, 0);
        capGradient.addColorStop(0, isMuted ? '#333' : '#444');
        capGradient.addColorStop(0.5, isMuted ? '#444' : '#666');
        capGradient.addColorStop(1, isMuted ? '#333' : '#444');
        ctx.fillStyle = capGradient;
        ctx.fillRect(capX, faderY, capWidth, capHeight);

        // Fader cap highlight line
        ctx.fillStyle = isMuted ? '#555' : color;
        ctx.fillRect(capX, faderY + capHeight / 2 - 1, capWidth, 2);

        // Border
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, width, height);

        // Store fader bounds for hit testing
        channel._faderBounds = {
            trackX: x,
            trackY: y,
            trackWidth: width,
            trackHeight: height,
            capX: capX,
            capY: faderY,
            capWidth: capWidth,
            capHeight: capHeight
        };
    }

    function renderButton(x, y, size, label, active, activeColor) {
        // Button background
        ctx.fillStyle = active ? activeColor : '#333';
        ctx.beginPath();
        ctx.roundRect(x, y, size, size, 4);
        ctx.fill();

        // Button border
        ctx.strokeStyle = active ? activeColor : '#555';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x, y, size, size, 4);
        ctx.stroke();

        // Button label
        ctx.fillStyle = active ? '#000' : '#888';
        ctx.font = 'bold 14px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, x + size / 2, y + size / 2);
        ctx.textBaseline = 'alphabetic';
    }

    function truncateText(text, maxWidth) {
        const measured = ctx.measureText(text);
        if (measured.width <= maxWidth) return text;

        while (text.length > 0 && ctx.measureText(text + '...').width > maxWidth) {
            text = text.slice(0, -1);
        }
        return text + '...';
    }

    // ==========================================================================
    // Event Handling
    // ==========================================================================

    function setupEventListeners() {
        // Mouse events
        canvas.addEventListener('mousedown', handlePointerDown);
        canvas.addEventListener('mousemove', handlePointerMove);
        canvas.addEventListener('mouseup', handlePointerUp);
        canvas.addEventListener('mouseleave', handlePointerUp);

        // Touch events
        canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
        canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
        canvas.addEventListener('touchend', handleTouchEnd);
        canvas.addEventListener('touchcancel', handleTouchEnd);

        // Window resize
        window.addEventListener('resize', () => {
            setupCanvas();
            render();
        });
    }

    function getCanvasCoords(e) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top
        };
    }

    function handlePointerDown(e) {
        if (!config.canOperate) return;

        const coords = getCanvasCoords(e);
        const hit = hitTest(coords.x, coords.y);

        if (hit) {
            if (hit.type === 'fader') {
                activeFader = {
                    channel: hit.channel,
                    startY: coords.y,
                    startLevel: hit.channel.current_level
                };
            } else if (hit.type === 'mute') {
                toggleMute(hit.channel);
            } else if (hit.type === 'solo') {
                toggleSolo(hit.channel);
            }
        }
    }

    function handlePointerMove(e) {
        if (!activeFader) return;

        const coords = getCanvasCoords(e);
        updateFaderFromDrag(coords.y);
    }

    function handlePointerUp(e) {
        if (activeFader) {
            // Send final value
            sendFaderUpdate(activeFader.channel, true);
            activeFader = null;
        }
    }

    function handleTouchStart(e) {
        e.preventDefault();
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            handlePointerDown({ clientX: touch.clientX, clientY: touch.clientY });
        }
    }

    function handleTouchMove(e) {
        e.preventDefault();
        if (e.touches.length === 1 && activeFader) {
            const touch = e.touches[0];
            const coords = getCanvasCoords({ clientX: touch.clientX, clientY: touch.clientY });
            updateFaderFromDrag(coords.y);
        }
    }

    function handleTouchEnd(e) {
        handlePointerUp(e);
    }

    function hitTest(x, y) {
        for (const channel of channels) {
            const bounds = channel._faderBounds;
            if (!bounds) continue;

            const index = channels.indexOf(channel);
            const stripX = STRIP_PADDING + index * (STRIP_WIDTH + STRIP_PADDING);
            const buttonY = FADER_TOP + FADER_HEIGHT + 20;

            // Check mute button
            if (x >= stripX + 10 && x <= stripX + 10 + BUTTON_SIZE &&
                y >= buttonY && y <= buttonY + BUTTON_SIZE) {
                return { type: 'mute', channel };
            }

            // Check solo button
            if (x >= stripX + STRIP_WIDTH - 10 - BUTTON_SIZE && x <= stripX + STRIP_WIDTH - 10 &&
                y >= buttonY && y <= buttonY + BUTTON_SIZE) {
                return { type: 'solo', channel };
            }

            // Check fader track
            if (x >= bounds.trackX && x <= bounds.trackX + bounds.trackWidth &&
                y >= bounds.trackY && y <= bounds.trackY + bounds.trackHeight) {
                return { type: 'fader', channel };
            }
        }
        return null;
    }

    function updateFaderFromDrag(currentY) {
        if (!activeFader) return;

        const channel = activeFader.channel;
        const bounds = channel._faderBounds;
        const minLevel = channel.min_level || 0;
        const maxLevel = channel.max_level || 127;
        const range = maxLevel - minLevel;

        // Calculate new level based on Y position
        const trackTop = bounds.trackY + 20;
        const trackBottom = bounds.trackY + bounds.trackHeight - 20;
        const trackRange = trackBottom - trackTop;

        const clampedY = Math.max(trackTop, Math.min(trackBottom, currentY));
        const normalizedLevel = 1 - (clampedY - trackTop) / trackRange;
        const newLevel = Math.round(minLevel + normalizedLevel * range);

        if (newLevel !== channel.current_level) {
            channel.current_level = newLevel;
            render();
            sendFaderUpdate(channel, false);
        }
    }

    function sendFaderUpdate(channel, isFinal) {
        const now = Date.now();

        // Rate limit to 10 updates per second, but always send final
        if (!isFinal && now - lastSendTime < 100) {
            pendingFaderValue = channel.current_level;
            return;
        }

        lastSendTime = now;
        pendingFaderValue = null;

        // Optimistically bump version to prevent stale poll data from reverting our change
        channelVersions[channel.id] = (channelVersions[channel.id] || 0) + 1;

        // Send via API and update version from response
        fetch(`${window.BASE_URL}/api/channel/${channel.id}/level`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({level: channel.current_level})
        }).then(r => r.json()).then(data => {
            if (data.version) {
                channelVersions[channel.id] = data.version;
            }
        }).catch(err => console.warn('Failed to update fader:', err));

        // Also try Socket.IO for faster sync if available
        if (socket && socket.connected) {
            socket.emit('fader_change', {
                channel_id: channel.id,
                level: channel.current_level,
                user_id: config.userId,
                is_final: isFinal
            });
        }

        // Send MIDI
        sendMidiFader(channel);
    }

    function toggleMute(channel) {
        channel.is_muted = !channel.is_muted;
        render();

        // Optimistically bump version to prevent stale poll data from reverting our change
        channelVersions[channel.id] = (channelVersions[channel.id] || 0) + 1;

        // Send via API and update version from response
        fetch(`${window.BASE_URL}/api/channel/${channel.id}/mute`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({is_muted: channel.is_muted})
        }).then(r => r.json()).then(data => {
            if (data.version) {
                channelVersions[channel.id] = data.version;
            }
        }).catch(err => console.warn('Failed to update mute:', err));

        // Also try Socket.IO
        if (socket && socket.connected) {
            socket.emit('mute_toggle', {
                channel_id: channel.id,
                is_muted: channel.is_muted,
                user_id: config.userId
            });
        }

        sendMidiMute(channel);
        recalculateMidiOutputs();
    }

    function toggleSolo(channel) {
        channel.is_solo = !channel.is_solo;
        render();

        // Optimistically bump version to prevent stale poll data from reverting our change
        channelVersions[channel.id] = (channelVersions[channel.id] || 0) + 1;

        // Send via API and update version from response
        fetch(`${window.BASE_URL}/api/channel/${channel.id}/solo`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({is_solo: channel.is_solo})
        }).then(r => r.json()).then(data => {
            if (data.version) {
                channelVersions[channel.id] = data.version;
            }
        }).catch(err => console.warn('Failed to update solo:', err));

        // Also try Socket.IO
        if (socket && socket.connected) {
            socket.emit('solo_toggle', {
                channel_id: channel.id,
                is_solo: channel.is_solo,
                user_id: config.userId
            });
        }

        sendMidiSolo(channel);
        recalculateMidiOutputs();
    }

    // ==========================================================================
    // MIDI
    // ==========================================================================

    function setupMidiControls() {
        document.getElementById('btn-midi-enable').addEventListener('click', enableMidi);
        document.getElementById('btn-midi-config').addEventListener('click', showMidiModal);
        document.getElementById('btn-midi-disable').addEventListener('click', disableMidi);
        document.getElementById('btn-midi-modal-cancel').addEventListener('click', hideMidiModal);
        document.getElementById('btn-midi-modal-save').addEventListener('click', saveMidiSettings);
    }

    async function enableMidi() {
        try {
            midiAccess = await navigator.requestMIDIAccess();
            midiEnabled = true;
            updateMidiUI();
            showMidiModal();
        } catch (err) {
            alert('MIDI access denied or not supported');
        }
    }

    function disableMidi() {
        midiEnabled = false;
        midiOutput = null;
        midiInput = null;
        localStorage.removeItem('faderbank_midi');
        updateMidiUI();
    }

    function showMidiModal() {
        if (!midiAccess) return;

        const outputSelect = document.getElementById('midi-output-device');
        const inputSelect = document.getElementById('midi-input-device');

        // Clear and populate output devices
        outputSelect.innerHTML = '<option value="">Select MIDI output...</option>';
        midiAccess.outputs.forEach((output, id) => {
            const option = document.createElement('option');
            option.value = id;
            option.textContent = output.name;
            if (midiOutput && midiOutput.id === id) option.selected = true;
            outputSelect.appendChild(option);
        });

        // Clear and populate input devices
        inputSelect.innerHTML = '<option value="">Select MIDI input (optional)...</option>';
        midiAccess.inputs.forEach((input, id) => {
            const option = document.createElement('option');
            option.value = id;
            option.textContent = input.name;
            if (midiInput && midiInput.id === id) option.selected = true;
            inputSelect.appendChild(option);
        });

        // Set channel
        document.getElementById('midi-channel').value = midiChannel;

        document.getElementById('midi-modal').style.display = 'flex';
    }

    function hideMidiModal() {
        document.getElementById('midi-modal').style.display = 'none';
    }

    function saveMidiSettings() {
        const outputId = document.getElementById('midi-output-device').value;
        const inputId = document.getElementById('midi-input-device').value;
        midiChannel = parseInt(document.getElementById('midi-channel').value);

        if (outputId && midiAccess) {
            midiOutput = midiAccess.outputs.get(outputId);
        } else {
            midiOutput = null;
        }

        if (inputId && midiAccess) {
            // Remove old listener
            if (midiInput) {
                midiInput.onmidimessage = null;
            }
            midiInput = midiAccess.inputs.get(inputId);
            midiInput.onmidimessage = handleMidiInput;
        } else {
            if (midiInput) {
                midiInput.onmidimessage = null;
            }
            midiInput = null;
        }

        // Save to localStorage
        localStorage.setItem('faderbank_midi', JSON.stringify({
            outputId,
            inputId,
            channel: midiChannel
        }));

        hideMidiModal();
        updateMidiUI();

        // Send current state to MIDI
        if (midiOutput) {
            channels.forEach(sendMidiFader);
        }
    }

    function loadMidiSettings() {
        const saved = localStorage.getItem('faderbank_midi');
        if (!saved) return;

        try {
            const settings = JSON.parse(saved);
            midiChannel = settings.channel || 1;

            // Will connect to devices when MIDI is enabled
            navigator.requestMIDIAccess().then(access => {
                midiAccess = access;
                midiEnabled = true;

                if (settings.outputId) {
                    midiOutput = access.outputs.get(settings.outputId);
                }
                if (settings.inputId) {
                    midiInput = access.inputs.get(settings.inputId);
                    if (midiInput) {
                        midiInput.onmidimessage = handleMidiInput;
                    }
                }

                updateMidiUI();
            }).catch(() => {
                // MIDI not available
            });
        } catch (e) {
            console.error('Error loading MIDI settings', e);
        }
    }

    function updateMidiUI() {
        document.getElementById('btn-midi-enable').style.display = midiEnabled ? 'none' : 'inline-flex';
        document.getElementById('btn-midi-config').style.display = midiEnabled ? 'inline-flex' : 'none';
        document.getElementById('btn-midi-disable').style.display = midiEnabled ? 'inline-flex' : 'none';
    }

    function sendMidiFader(channel) {
        if (!midiOutput || !midiEnabled) return;

        const anySolo = channels.some(c => c.is_solo);
        const isEffectivelyMuted = channel.is_muted || (anySolo && !channel.is_solo);

        // Send 0 if effectively muted, otherwise send the actual level
        const outputLevel = isEffectivelyMuted ? 0 : channel.current_level;

        // Send CC message: [status, cc number, value]
        const status = 0xB0 + (midiChannel - 1);  // CC message on channel
        midiOutput.send([status, channel.midi_cc_output, outputLevel]);
    }

    function sendMidiMute(channel) {
        if (!midiOutput || !midiEnabled || !channel.midi_cc_mute) return;

        const status = 0xB0 + (midiChannel - 1);
        const value = channel.is_muted ? 127 : 0;
        midiOutput.send([status, channel.midi_cc_mute, value]);
    }

    function sendMidiSolo(channel) {
        if (!midiOutput || !midiEnabled || !channel.midi_cc_solo) return;

        const status = 0xB0 + (midiChannel - 1);
        const value = channel.is_solo ? 127 : 0;
        midiOutput.send([status, channel.midi_cc_solo, value]);
    }

    function recalculateMidiOutputs() {
        // When mute/solo state changes, recalculate all fader outputs
        if (!midiOutput || !midiEnabled) return;

        channels.forEach(sendMidiFader);
    }

    function handleMidiInput(message) {
        const [status, cc, value] = message.data;

        // Update debug display with raw MIDI data
        const msgType = (status & 0xF0) === 0xB0 ? 'CC' : 'Other';
        const msgChan = (status & 0x0F) + 1;
        updateMidiDebug(`${msgType} Ch:${msgChan} CC:${cc} Val:${value}`);

        // Check if it's a CC message on our channel
        const expectedStatus = 0xB0 + (midiChannel - 1);
        if (status !== expectedStatus) return;

        // Find channel by VU CC
        const channel = channels.find(c => c.midi_cc_vu_input === cc);
        if (!channel) return;

        // Track peak value
        const now = Date.now();
        if (!vuPeaks[channel.id]) {
            vuPeaks[channel.id] = { peak: 0, lastUpdate: 0 };
        }

        const peak = vuPeaks[channel.id];
        peak.peak = Math.max(peak.peak, value);

        // Send peak every 100ms
        if (now - peak.lastUpdate >= VU_PEAK_INTERVAL) {
            channel.vu_level = peak.peak;

            // Broadcast to other users
            socket.emit('vu_level', {
                channel_id: channel.id,
                level: peak.peak
            });

            peak.peak = 0;
            peak.lastUpdate = now;
        }
    }

    // ==========================================================================
    // Responsibility
    // ==========================================================================

    function setupResponsibilityControls() {
        document.getElementById('btn-take-responsibility').addEventListener('click', async () => {
            try {
                const response = await fetch(`${window.BASE_URL}/api/profile/${config.profileId}/responsibility/take`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await response.json();

                if (data.success) {
                    // Update local state immediately
                    responsibilityUser = {
                        user_id: config.userId,
                        display_name: config.displayName
                    };
                    updateResponsibilityUI();
                    render();
                } else if (data.current_user) {
                    // Someone else has it - show confirm dialog
                    showResponsibilityConfirmModal(data.current_user);
                } else {
                    console.warn('Failed to take responsibility:', data.error);
                }
            } catch (err) {
                console.warn('Failed to take responsibility:', err);
            }
        });

        document.getElementById('btn-drop-responsibility').addEventListener('click', async () => {
            try {
                await fetch(`${window.BASE_URL}/api/profile/${config.profileId}/responsibility/drop`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                // Update local state immediately
                responsibilityUser = null;
                updateResponsibilityUI();
                render();
            } catch (err) {
                console.warn('Failed to drop responsibility:', err);
            }
        });

        document.getElementById('btn-responsibility-cancel').addEventListener('click', hideResponsibilityModal);
        document.getElementById('btn-responsibility-confirm').addEventListener('click', async () => {
            hideResponsibilityModal();
            // Force take - we need to drop the other person's responsibility first via a force endpoint
            // For now, the regular take will work since we're just updating the DB
            try {
                // Use a direct DB update approach - take_responsibility replaces whoever has it
                await fetch(`${window.BASE_URL}/api/profile/${config.profileId}/responsibility/take?force=1`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                responsibilityUser = {
                    user_id: config.userId,
                    display_name: config.displayName
                };
                updateResponsibilityUI();
                render();
            } catch (err) {
                console.warn('Failed to force take responsibility:', err);
            }
        });
    }

    function showResponsibilityConfirmModal(currentUserName) {
        document.getElementById('responsibility-modal-text').textContent =
            `${currentUserName} currently has responsibility. Take it anyway?`;
        document.getElementById('responsibility-modal').style.display = 'flex';
    }

    function hideResponsibilityModal() {
        document.getElementById('responsibility-modal').style.display = 'none';
    }

    function updateResponsibilityUI() {
        const statusEl = document.getElementById('responsibility-status');
        const takeBtn = document.getElementById('btn-take-responsibility');
        const dropBtn = document.getElementById('btn-drop-responsibility');

        if (responsibilityUser) {
            if (responsibilityUser.user_id === config.userId) {
                statusEl.textContent = 'You have responsibility';
                takeBtn.style.display = 'none';
                dropBtn.style.display = 'inline-flex';
            } else {
                statusEl.textContent = `${responsibilityUser.display_name} has responsibility`;
                takeBtn.style.display = config.canOperate ? 'inline-flex' : 'none';
                dropBtn.style.display = 'none';
            }
        } else {
            statusEl.textContent = 'No one has responsibility';
            takeBtn.style.display = config.canOperate ? 'inline-flex' : 'none';
            dropBtn.style.display = 'none';
        }

        // Also update online users list to sync responsibility emoji
        updateOnlineUsersList();
    }

    // ==========================================================================
    // Online Users
    // ==========================================================================

    function updateOnlineUsersList() {
        const list = document.getElementById('online-users');
        list.innerHTML = '';

        Object.entries(onlineUsers).forEach(([userId, user]) => {
            const li = document.createElement('li');

            const indicator = document.createElement('span');
            indicator.className = 'user-online-indicator';
            li.appendChild(indicator);

            const name = document.createElement('span');
            const hasResponsibility = responsibilityUser && responsibilityUser.user_id === parseInt(userId);
            if (hasResponsibility) {
                name.className = 'user-has-responsibility';
                name.textContent = '🎛️ ' + user.display_name;
            } else {
                name.textContent = user.display_name;
            }
            li.appendChild(name);

            list.appendChild(li);
        });
    }

    function updateMidiDebug(message) {
        const debugEl = document.getElementById('midi-debug');
        if (debugEl) {
            debugEl.textContent = 'MIDI: ' + message;
        }
    }

    // ==========================================================================
    // Animation Loop
    // ==========================================================================

    function animationLoop() {
        // Check for pending fader updates
        if (pendingFaderValue !== null && activeFader) {
            const now = Date.now();
            if (now - lastSendTime >= 100) {
                sendFaderUpdate(activeFader.channel, false);
            }
        }

        // Decay VU levels
        let needsRender = false;
        channels.forEach(channel => {
            if (channel.vu_level > 0) {
                channel.vu_level = Math.max(0, channel.vu_level - 3);
                needsRender = true;
            }
        });

        if (needsRender) {
            render();
        }

        requestAnimationFrame(animationLoop);
    }

    // ==========================================================================
    // Initialize
    // ==========================================================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
