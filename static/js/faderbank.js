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
    const CUSTOM_BUTTON_HEIGHT = 28;
    const CUSTOM_BUTTON_GAP = 4;

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
    let buttons = [...(config.buttons || [])];
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

    // VU broadcast buffer (for sending to server)
    const vuBuffer = {};  // channel_id -> level
    let vuBroadcastTimer = null;
    const VU_BROADCAST_INTERVAL = 100;  // Send VU updates every 100ms
    const localVuChannels = new Set();  // Channels receiving local MIDI VU (don't overwrite from polling)
    const lastReceivedVu = {};  // channel_id -> last received VU level (for stuck signal detection)

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
        updateResponsibilityUI();
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

                // Only apply versioned state if server has newer version
                if (serverVersion > localVersion) {
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

                // VU levels: apply from server unless this channel receives local MIDI VU
                // Only update if the received level changed (allows stuck signals to decay)
                if (update.vu_level !== undefined && !localVuChannels.has(channel.id)) {
                    const prevReceived = lastReceivedVu[channel.id];
                    const newReceived = update.vu_level;

                    // Only update display if the received value changed from last time
                    if (prevReceived !== newReceived) {
                        lastReceivedVu[channel.id] = newReceived;
                        // Only set if new level is higher than current (decayed) level,
                        // or if we need to show the new value
                        if (newReceived > channel.vu_level || channel.vu_level === 0) {
                            channel.vu_level = newReceived;
                            needsRender = true;
                        }
                    }
                    // If received level is same as before, let decay continue naturally

                    // Handle right channel VU if present
                    if (update.vu_level_right !== undefined) {
                        const prevRight = lastReceivedVu[`${channel.id}_right`];
                        const newRight = update.vu_level_right;
                        if (prevRight !== newRight) {
                            lastReceivedVu[`${channel.id}_right`] = newRight;
                            if (newRight > (channel.vu_level_right || 0) || channel.vu_level_right === 0) {
                                channel.vu_level_right = newRight;
                                needsRender = true;
                            }
                        }
                    }
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

        // Calculate required width (account for buttons strip if present)
        const unassignedButtons = buttons.filter(b => !b.channel_strip_id);
        const buttonsStripWidth = unassignedButtons.length > 0 ? STRIP_WIDTH + STRIP_PADDING : 0;
        const minWidth = buttonsStripWidth + channels.length * (STRIP_WIDTH + STRIP_PADDING) + STRIP_PADDING;
        const width = Math.max(rect.width, minWidth);

        // Calculate required height based on content
        // Base height: fader + mute/solo buttons + dB label
        const baseChannelHeight = FADER_TOP + FADER_HEIGHT + 20 + BUTTON_SIZE + 35;

        // Find the channel with the most custom buttons to determine max height
        let maxChannelButtons = 0;
        channels.forEach(ch => {
            const channelButtons = buttons.filter(b => b.channel_strip_id === ch.id);
            maxChannelButtons = Math.max(maxChannelButtons, channelButtons.length);
        });

        // Height needed for channel strips (with their custom buttons)
        const channelStripHeight = baseChannelHeight + maxChannelButtons * (CUSTOM_BUTTON_HEIGHT + CUSTOM_BUTTON_GAP);

        // Height needed for unassigned buttons strip
        const buttonsStripHeight = unassignedButtons.length > 0
            ? FADER_TOP + unassignedButtons.length * (CUSTOM_BUTTON_HEIGHT + CUSTOM_BUTTON_GAP)
            : 0;

        // Minimum height is the max of channel strip height, buttons strip height, plus padding
        const minHeight = Math.max(channelStripHeight, buttonsStripHeight) + 20;
        const height = Math.max(rect.height, minHeight);

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
                if (data.level_right !== undefined) {
                    channel.vu_level_right = data.level_right;
                }
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

        // Button events
        socket.on('button_pressed', (data) => {
            console.log('Received button_pressed event:', data);
            // Update local button state for toggle buttons
            const btn = buttons.find(b => b.id === data.button_id);
            if (btn && data.new_state !== null) {
                btn.is_on = data.new_state;
            }
            render();

            // Send MIDI if enabled
            console.log('MIDI output enabled:', midiEnabled, 'midiOutput:', midiOutput ? 'connected' : 'none');
            if (midiOutput && midiEnabled) {
                sendButtonMidi(data);
            }
        });

        socket.on('button_created', (data) => {
            buttons.push(data.button);
            render();
        });

        socket.on('button_updated', (data) => {
            const index = buttons.findIndex(b => b.id === data.button.id);
            if (index !== -1) {
                buttons[index] = data.button;
                render();
            }
        });

        socket.on('button_deleted', (data) => {
            buttons = buttons.filter(b => b.id !== data.button_id);
            render();
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

        // Get unassigned buttons
        const unassignedButtons = buttons.filter(b => !b.channel_strip_id);
        const hasUnassignedButtons = unassignedButtons.length > 0;

        // Calculate x offset (shift channels right if there's a buttons strip)
        const xOffset = hasUnassignedButtons ? STRIP_WIDTH + STRIP_PADDING : 0;

        // Render unassigned buttons strip (if any)
        if (hasUnassignedButtons) {
            renderButtonsStrip(unassignedButtons, STRIP_PADDING);
        }

        // Render each channel strip
        channels.forEach((channel, index) => {
            const channelButtons = buttons.filter(b => b.channel_strip_id === channel.id);
            renderChannelStrip(channel, index, anySolo, xOffset, channelButtons);
        });
    }

    function renderChannelStrip(channel, index, anySolo, xOffset = 0, channelButtons = []) {
        const x = xOffset + STRIP_PADDING + index * (STRIP_WIDTH + STRIP_PADDING);
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

        // Custom buttons for this channel
        if (channelButtons.length > 0) {
            let customButtonY = buttonY + BUTTON_SIZE + 35;
            channelButtons.forEach(btn => {
                renderCustomButton(btn, x + 5, customButtonY, STRIP_WIDTH - 10, CUSTOM_BUTTON_HEIGHT);
                customButtonY += CUSTOM_BUTTON_HEIGHT + CUSTOM_BUTTON_GAP;
            });
        }
    }

    function renderVUMeter(channel, x, y, width, height, isMuted) {
        const isStereo = channel.midi_cc_vu_input_right !== null && channel.midi_cc_vu_input_right !== undefined;

        // Background
        ctx.fillStyle = '#0a0a15';
        ctx.fillRect(x, y, width, height);

        if (isStereo) {
            // Stereo: two bars side by side
            const barWidth = Math.floor((width - 1) / 2);  // 1px gap in middle
            renderVUBar(channel.vu_level, channel.vu_peak, x, y, barWidth, height, isMuted);
            renderVUBar(channel.vu_level_right, channel.vu_peak_right, x + barWidth + 1, y, barWidth, height, isMuted);
        } else {
            // Mono: single bar
            renderVUBar(channel.vu_level, channel.vu_peak, x, y, width, height, isMuted);
        }

        // Border
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, width, height);
    }

    function renderVUBar(level, peak, x, y, width, height, isMuted) {
        if (level !== undefined && level > 0) {
            // Apply power curve to compress quiet values (counteracts dB boost from source)
            const rawLevel = level / 127;
            const normalizedLevel = Math.pow(rawLevel, 2);  // Square to compress quiet signals
            const meterHeight = height * normalizedLevel;

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

        // Peak indicator line
        if (peak !== undefined && peak > 0) {
            const rawPeak = peak / 127;
            const peakLevel = Math.pow(rawPeak, 2);  // Same power curve as main meter
            const peakY = y + height - (height * peakLevel);

            ctx.save();
            if (isMuted) {
                ctx.globalAlpha = 0.3;
            }

            // Color based on peak height (same thresholds as gradient)
            let peakColor;
            if (peakLevel > 0.8) {
                peakColor = '#ef4444';  // Red
            } else if (peakLevel > 0.6) {
                peakColor = '#eab308';  // Yellow
            } else {
                peakColor = '#22c55e';  // Green
            }

            ctx.strokeStyle = peakColor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x + 1, peakY);
            ctx.lineTo(x + width - 1, peakY);
            ctx.stroke();

            ctx.restore();
        }
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

    function renderButtonsStrip(btns, x) {
        // Strip background
        ctx.fillStyle = 'rgba(30, 30, 50, 0.5)';
        ctx.fillRect(x, 0, STRIP_WIDTH, canvas.displayHeight);

        // Render each button
        let buttonY = FADER_TOP;
        btns.forEach(btn => {
            renderCustomButton(btn, x + 5, buttonY, STRIP_WIDTH - 10, CUSTOM_BUTTON_HEIGHT);
            buttonY += CUSTOM_BUTTON_HEIGHT + CUSTOM_BUTTON_GAP;
        });
    }

    function renderCustomButton(btn, x, y, width, height) {
        const isOn = btn.is_on;
        const isToggle = btn.mode === 'toggle';

        // Button background
        ctx.fillStyle = isOn ? '#3b82f6' : '#333';
        ctx.beginPath();
        ctx.roundRect(x, y, width, height, 4);
        ctx.fill();

        // Button border
        ctx.strokeStyle = isOn ? '#60a5fa' : '#555';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x, y, width, height, 4);
        ctx.stroke();

        // Button label
        ctx.fillStyle = isOn ? '#fff' : '#aaa';
        ctx.font = '11px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(truncateText(btn.label, width - 8), x + width / 2, y + height / 2);
        ctx.textBaseline = 'alphabetic';

        // Store button bounds for hit testing
        btn._bounds = { x, y, width, height };
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

        // Touch events - only touchstart initially, touchmove added dynamically when dragging
        canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
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

        // First check for custom button clicks
        const clickedButton = hitTestButton(coords.x, coords.y);
        if (clickedButton) {
            pressButton(clickedButton);
            return;
        }

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
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const coords = getCanvasCoords({ clientX: touch.clientX, clientY: touch.clientY });

            // Check for custom button hit first
            const clickedButton = hitTestButton(coords.x, coords.y);
            if (clickedButton) {
                e.preventDefault();
                pressButton(clickedButton);
                return;
            }

            const hit = hitTest(coords.x, coords.y);

            // Only prevent scrolling if touching an interactive element
            if (hit) {
                e.preventDefault();
                handlePointerDown({ clientX: touch.clientX, clientY: touch.clientY });

                // Add touchmove listener only when dragging a fader
                if (hit.type === 'fader') {
                    canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
                }
            }
            // Otherwise allow normal touch behavior (scrolling)
        }
    }

    function handleTouchMove(e) {
        if (e.touches.length === 1 && activeFader) {
            e.preventDefault();
            const touch = e.touches[0];
            const coords = getCanvasCoords({ clientX: touch.clientX, clientY: touch.clientY });
            updateFaderFromDrag(coords.y);
        }
    }

    function handleTouchEnd(e) {
        // Remove touchmove listener when touch ends
        canvas.removeEventListener('touchmove', handleTouchMove);
        handlePointerUp(e);
    }

    function hitTest(x, y) {
        // Calculate xOffset same as render() does
        const unassignedButtons = buttons.filter(b => !b.channel_strip_id);
        const hasUnassignedButtons = unassignedButtons.length > 0;
        const xOffset = hasUnassignedButtons ? STRIP_WIDTH + STRIP_PADDING : 0;

        for (const channel of channels) {
            const bounds = channel._faderBounds;
            if (!bounds) continue;

            const index = channels.indexOf(channel);
            const stripX = xOffset + STRIP_PADDING + index * (STRIP_WIDTH + STRIP_PADDING);
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

    function hitTestButton(x, y) {
        // Check all buttons for hit
        for (const btn of buttons) {
            if (btn._bounds) {
                const b = btn._bounds;
                if (x >= b.x && x <= b.x + b.width &&
                    y >= b.y && y <= b.y + b.height) {
                    return btn;
                }
            }
        }
        return null;
    }

    async function pressButton(btn) {
        console.log('Button clicked:', btn.label, 'id:', btn.id, 'bounds:', btn._bounds);
        try {
            const response = await fetch(`${BASE_URL}/api/button/${btn.id}/press`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            console.log('Button press response:', data);
            if (!data.success) {
                console.error('Failed to press button:', data.error);
            }
            // Server will broadcast the button_pressed event to all clients
        } catch (e) {
            console.error('Error pressing button:', e);
        }
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
        document.getElementById('midi-debug').style.display = midiEnabled ? 'block' : 'none';
    }

    function getEffectiveMidiChannel(item) {
        // Use item-specific midi_channel if set, otherwise use global default
        return item.midi_channel ? (item.midi_channel - 1) : (midiChannel - 1);
    }

    function sendMidiFader(channel) {
        if (!midiOutput || !midiEnabled) return;

        const anySolo = channels.some(c => c.is_solo);
        const isEffectivelyMuted = channel.is_muted || (anySolo && !channel.is_solo);

        // Send 0 if effectively muted, otherwise send the actual level
        const outputLevel = isEffectivelyMuted ? 0 : channel.current_level;

        // Send CC message: [status, cc number, value]
        const chan = getEffectiveMidiChannel(channel);
        const status = 0xB0 + chan;
        midiOutput.send([status, channel.midi_cc_output, outputLevel]);
    }

    function sendMidiMute(channel) {
        if (!midiOutput || !midiEnabled || !channel.midi_cc_mute) return;

        const chan = getEffectiveMidiChannel(channel);
        const status = 0xB0 + chan;
        const value = channel.is_muted ? 127 : 0;
        midiOutput.send([status, channel.midi_cc_mute, value]);
    }

    function sendMidiSolo(channel) {
        if (!midiOutput || !midiEnabled || !channel.midi_cc_solo) return;

        const chan = getEffectiveMidiChannel(channel);
        const status = 0xB0 + chan;
        const value = channel.is_solo ? 127 : 0;
        midiOutput.send([status, channel.midi_cc_solo, value]);
    }

    function sendButtonMidi(data) {
        if (!midiOutput || !midiEnabled) return;

        const midiType = data.midi_type || 'cc';
        const chan = data.midi_channel ? (data.midi_channel - 1) : (midiChannel - 1);
        console.log('sendButtonMidi:', data.mode, 'type:', midiType, 'ch:', chan + 1, 'num:', data.midi_cc, 'on:', data.on_value, 'off:', data.off_value, 'state:', data.new_state);

        if (data.mode === 'momentary') {
            // Momentary: send on message, then off message after short delay
            sendButtonMessage(midiType, chan, data.midi_cc, data.on_value, true);
            setTimeout(() => {
                if (midiOutput && midiEnabled) {
                    sendButtonMessage(midiType, chan, data.midi_cc, data.off_value, false);
                }
            }, 50);
        } else {
            // Toggle: send based on new_state
            const value = data.new_state ? data.on_value : data.off_value;
            sendButtonMessage(midiType, chan, data.midi_cc, value, data.new_state);
        }
    }

    function sendButtonMessage(midiType, channel, number, value, isOn) {
        let msg;
        if (midiType === 'note') {
            // Note On: 0x90, Note Off: 0x80
            const status = (isOn && value > 0) ? (0x90 + channel) : (0x80 + channel);
            msg = [status, number, value];
        } else if (midiType === 'pc') {
            // Program Change: 0xC0 (only 2 bytes, no velocity)
            const status = 0xC0 + channel;
            msg = [status, value];
        } else {
            // CC: 0xB0
            const status = 0xB0 + channel;
            msg = [status, number, value];
        }
        console.log('Sending MIDI:', msg);
        midiOutput.send(msg);
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

        // Find channel by VU CC (left/mono or right)
        let channel = channels.find(c => c.midi_cc_vu_input === cc);
        let isRight = false;
        if (!channel) {
            channel = channels.find(c => c.midi_cc_vu_input_right === cc);
            isRight = true;
        }
        if (!channel) return;

        // Track peak value
        const now = Date.now();
        const peakKey = isRight ? `${channel.id}_right` : channel.id;
        if (!vuPeaks[peakKey]) {
            vuPeaks[peakKey] = { peak: 0, lastUpdate: 0 };
        }

        const peak = vuPeaks[peakKey];
        peak.peak = Math.max(peak.peak, value);

        // Mark this channel as receiving local VU (don't overwrite from polling)
        localVuChannels.add(channel.id);

        // Send peak every 100ms
        if (now - peak.lastUpdate >= VU_PEAK_INTERVAL) {
            if (isRight) {
                channel.vu_level_right = peak.peak;
            } else {
                channel.vu_level = peak.peak;
            }

            // Buffer for broadcast to server (stereo format if right channel exists)
            if (!vuBuffer[channel.id]) {
                vuBuffer[channel.id] = { left: channel.vu_level || 0 };
            }
            if (isRight) {
                vuBuffer[channel.id].right = peak.peak;
            } else {
                vuBuffer[channel.id].left = peak.peak;
            }
            scheduleVuBroadcast();

            peak.peak = 0;
            peak.lastUpdate = now;
        }
    }

    function scheduleVuBroadcast() {
        if (vuBroadcastTimer) return;  // Already scheduled

        vuBroadcastTimer = setTimeout(async () => {
            vuBroadcastTimer = null;

            // Send buffered VU levels to server
            if (Object.keys(vuBuffer).length > 0) {
                try {
                    await fetch(`${window.BASE_URL}/api/profile/${config.profileId}/vu`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ levels: vuBuffer })
                    });
                } catch (err) {
                    // Silently ignore VU broadcast errors
                }

                // Clear buffer after sending
                for (const key in vuBuffer) {
                    delete vuBuffer[key];
                }
            }
        }, VU_BROADCAST_INTERVAL);
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
    // Online Users (Operators and Guests)
    // ==========================================================================

    function updateOnlineUsersList() {
        const operatorsList = document.getElementById('operators-list');
        const guestsList = document.getElementById('guests-list');
        const guestsSection = document.getElementById('guests-section');

        operatorsList.innerHTML = '';
        guestsList.innerHTML = '';

        const operators = [];
        const guests = [];

        // Split users by role
        Object.entries(onlineUsers).forEach(([userId, user]) => {
            const userWithId = { ...user, id: userId };
            if (user.role === 'guest') {
                guests.push(userWithId);
            } else {
                // owner, admin, technician, operator all go in operators list
                operators.push(userWithId);
            }
        });

        // Render operators
        operators.forEach(user => {
            operatorsList.appendChild(createUserListItem(user));
        });

        // If no operators, show a placeholder
        if (operators.length === 0) {
            const li = document.createElement('li');
            li.className = 'user-list-empty';
            li.textContent = 'No operators recently';
            operatorsList.appendChild(li);
        }

        // Render guests section (only show if there are guests)
        if (guests.length > 0) {
            guestsSection.style.display = 'block';
            guests.forEach(user => {
                guestsList.appendChild(createUserListItem(user));
            });
        } else {
            guestsSection.style.display = 'none';
        }
    }

    function createUserListItem(user) {
        const li = document.createElement('li');
        li.style.display = 'flex';
        li.style.alignItems = 'center';
        const secondsAgo = user.seconds_ago || 0;

        // Determine activity state:
        // - Active (green dot): seen in last 30 seconds
        // - Inactive (gray dot): seen 30 seconds to 3 minutes ago
        // - Faded (gray dot, faded name): seen more than 3 minutes ago
        const isActive = secondsAgo <= 30;
        const isFaded = secondsAgo > 180; // 3 minutes

        if (isFaded) {
            li.className = 'user-inactive-faded';
        }

        // Always show dot, but change color based on status
        const indicator = document.createElement('span');
        indicator.className = isActive ? 'user-online-indicator' : 'user-offline-indicator';
        li.appendChild(indicator);

        const name = document.createElement('span');
        const hasResponsibility = responsibilityUser && responsibilityUser.user_id === parseInt(user.id);

        // Build name with role emoji prefix
        let prefix = '';
        if (user.role === 'owner') {
            prefix = '👑 ';
        } else if (user.role === 'admin') {
            prefix = '🔑 ';
        } else if (user.role === 'technician') {
            prefix = '🔧 ';
        }

        if (hasResponsibility) {
            name.className = 'user-has-responsibility';
        }

        name.textContent = prefix + user.display_name;
        li.appendChild(name);

        // Add "(you)" indicator for current user
        if (parseInt(user.id) === config.userId) {
            const youSpan = document.createElement('span');
            youSpan.className = 'user-you-indicator';
            youSpan.textContent = ' (you)';
            li.appendChild(youSpan);
        }

        // Add responsibility emoji at far right
        if (hasResponsibility) {
            const spacer = document.createElement('span');
            spacer.style.flexGrow = '1';
            li.appendChild(spacer);

            const respEmoji = document.createElement('span');
            respEmoji.textContent = '🎛️';
            respEmoji.title = 'Has responsibility';
            li.appendChild(respEmoji);
        }

        return li;
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

    let vuDecayFrameCount = 0;
    const VU_PEAK_HOLD_MS = 1500;  // Hold peak for 1.5 seconds before decay

    function animationLoop() {
        // Check for pending fader updates
        if (pendingFaderValue !== null && activeFader) {
            const now = Date.now();
            if (now - lastSendTime >= 100) {
                sendFaderUpdate(activeFader.channel, false);
            }
        }

        const now = Date.now();

        // Decay VU levels (every 5 frames - doubled speed)
        vuDecayFrameCount++;
        let needsRender = false;
        if (vuDecayFrameCount >= 5) {
            vuDecayFrameCount = 0;
            channels.forEach(channel => {
                // Decay main VU level (left/mono)
                if (channel.vu_level > 0) {
                    channel.vu_level = Math.max(0, channel.vu_level - 1);
                    needsRender = true;
                }

                // Decay right VU level (if stereo)
                if (channel.vu_level_right > 0) {
                    channel.vu_level_right = Math.max(0, channel.vu_level_right - 1);
                    needsRender = true;
                }

                // Update peak tracking (left/mono)
                if (channel.vu_level > (channel.vu_peak || 0)) {
                    channel.vu_peak = channel.vu_level;
                    channel.vu_peak_time = now;
                }

                // Update peak tracking (right)
                if (channel.vu_level_right > (channel.vu_peak_right || 0)) {
                    channel.vu_peak_right = channel.vu_level_right;
                    channel.vu_peak_right_time = now;
                }

                // Decay peak after hold time expires (left/mono)
                if (channel.vu_peak > 0) {
                    const peakAge = now - (channel.vu_peak_time || 0);
                    if (peakAge > VU_PEAK_HOLD_MS) {
                        // Faster decay for peak indicator (2 units per cycle)
                        channel.vu_peak = Math.max(0, channel.vu_peak - 2);
                        needsRender = true;
                    }
                }

                // Decay peak after hold time expires (right)
                if (channel.vu_peak_right > 0) {
                    const peakAge = now - (channel.vu_peak_right_time || 0);
                    if (peakAge > VU_PEAK_HOLD_MS) {
                        channel.vu_peak_right = Math.max(0, channel.vu_peak_right - 2);
                        needsRender = true;
                    }
                }
            });
        }

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
