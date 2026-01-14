-- Zebby Faderbank Database Schema
-- Database: zebby_faderbank

-- Users (synced from Zebby)
CREATE TABLE user (
    id INT PRIMARY KEY,                    -- Matches Zebby user_id
    username VARCHAR(255),                 -- Cached from Zebby for display
    display_name VARCHAR(255),             -- Cached from Zebby for display
    last_active_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Fader bank profiles
CREATE TABLE profile (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,     -- URL-friendly identifier, globally unique
    owner_id INT NOT NULL,                 -- User who created the profile
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES user(id)
);

-- Profile membership and roles
CREATE TABLE profile_member (
    id INT AUTO_INCREMENT PRIMARY KEY,
    profile_id INT NOT NULL,
    user_id INT NOT NULL,
    role ENUM('owner', 'admin', 'technician', 'operator', 'guest') NOT NULL,
    added_by INT,                          -- User who invited them
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES profile(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (added_by) REFERENCES user(id),
    UNIQUE KEY unique_membership (profile_id, user_id)
);

-- Single-use activation links for inviting users
-- Only consumed when actually redeemed (not by viewing while logged out or if already a member)
CREATE TABLE activation_link (
    id INT AUTO_INCREMENT PRIMARY KEY,
    profile_id INT NOT NULL,
    token VARCHAR(64) NOT NULL UNIQUE,     -- Random token for the URL
    role ENUM('admin', 'technician', 'operator', 'guest') NOT NULL,
    created_by INT NOT NULL,               -- User who created the link
    used_by INT,                           -- User who redeemed it (NULL if unused)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,          -- 7 days from creation
    canceled_at DATETIME,                  -- NULL if not canceled by owner
    used_at DATETIME,                      -- NULL if unused
    FOREIGN KEY (profile_id) REFERENCES profile(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES user(id),
    FOREIGN KEY (used_by) REFERENCES user(id)
);

-- Channel strips within a profile
CREATE TABLE channel_strip (
    id INT AUTO_INCREMENT PRIMARY KEY,
    profile_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    position INT NOT NULL,                 -- Order in the fader bank (0-based)
    color ENUM('red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple', 'white') DEFAULT 'white',
    midi_cc_output INT NOT NULL,           -- CC number for fader output (0-127)
    midi_cc_vu_input INT,                  -- CC number for VU level input (NULL if not used)
    midi_cc_mute INT,                      -- CC number for mute button (NULL if not used)
    midi_cc_solo INT,                      -- CC number for solo button (NULL if not used)
    min_level INT DEFAULT 0,               -- Minimum fader value (0-127)
    max_level INT DEFAULT 127,             -- Maximum fader value (0-127)
    current_level INT DEFAULT 0,           -- Current fader position (0-127)
    is_muted BOOLEAN DEFAULT FALSE,        -- Mute button state
    is_solo BOOLEAN DEFAULT FALSE,         -- Solo button state
    state_version INT DEFAULT 0,           -- Increments on every state change (level/mute/solo)
    vu_level INT DEFAULT 0,                -- Current VU meter level (0-127, ephemeral)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES profile(id) ON DELETE CASCADE,
    INDEX idx_profile_position (profile_id, position)
);

-- Track who has "responsibility" for each profile
CREATE TABLE profile_responsibility (
    profile_id INT PRIMARY KEY,
    user_id INT,                           -- NULL if no one has responsibility
    taken_at DATETIME,
    FOREIGN KEY (profile_id) REFERENCES profile(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES user(id)
);

-- Track active users per profile (for online users list)
CREATE TABLE profile_activity (
    profile_id INT NOT NULL,
    user_id INT NOT NULL,
    last_seen_at DATETIME NOT NULL,
    PRIMARY KEY (profile_id, user_id),
    FOREIGN KEY (profile_id) REFERENCES profile(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES user(id)
);

-- Session table for ephemeral data
CREATE TABLE session (
    session_id VARCHAR(255) PRIMARY KEY,
    created_at DATETIME NOT NULL,
    last_accessed_at DATETIME NOT NULL,
    data JSON
);
