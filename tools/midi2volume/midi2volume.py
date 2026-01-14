#!/usr/bin/env python3
"""
MIDI to macOS System Volume Controller

Listens to MIDI CC messages and controls macOS system master volume.

Usage:
    python midi2volume.py -m "IAC Driver Bus 1" -c 1 --cc 7
    python midi2volume.py --list-midi
"""

import argparse
import subprocess
import sys
import time
import threading

try:
    import rtmidi
except ImportError:
    print("Error: python-rtmidi not installed. Run: pip install python-rtmidi")
    sys.exit(1)


def list_midi_ports():
    """List available MIDI input ports."""
    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()

    if not ports:
        print("No MIDI input ports found.")
        return

    print("Available MIDI input ports:")
    for i, port in enumerate(ports):
        print(f"  {i}: {port}")


def set_macos_volume(level):
    """Set macOS system volume (0-100)."""
    try:
        # Clamp to valid range
        level = max(0, min(100, level))
        subprocess.run(
            ['osascript', '-e', f'set volume output volume {level}'],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error setting volume: {e}")
        return False
    except FileNotFoundError:
        print("Error: osascript not found. This tool only works on macOS.")
        return False


def get_macos_volume():
    """Get current macOS system volume (0-100)."""
    try:
        result = subprocess.run(
            ['osascript', '-e', 'output volume of (get volume settings)'],
            check=True,
            capture_output=True,
            text=True
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


class MidiVolumeController:
    def __init__(self, midi_port, midi_channel, cc_number, invert=False, debounce_ms=100):
        self.midi_port = midi_port
        self.midi_channel = midi_channel - 1  # Convert to 0-indexed
        self.cc_number = cc_number
        self.invert = invert
        self.debounce_ms = debounce_ms
        self.running = False
        self.midi_in = None
        self.last_volume = None
        self.pending_volume = None
        self.last_update_time = 0
        self.debounce_timer = None

    def start(self):
        """Start listening for MIDI messages."""
        self.midi_in = rtmidi.MidiIn()
        ports = self.midi_in.get_ports()

        # Find the port
        port_index = None
        if isinstance(self.midi_port, int):
            if self.midi_port < len(ports):
                port_index = self.midi_port
        else:
            for i, port in enumerate(ports):
                if self.midi_port.lower() in port.lower():
                    port_index = i
                    break

        if port_index is None:
            print(f"Error: MIDI port '{self.midi_port}' not found.")
            print("Available ports:")
            for i, port in enumerate(ports):
                print(f"  {i}: {port}")
            return False

        self.midi_in.open_port(port_index)
        self.midi_in.set_callback(self.midi_callback)
        self.running = True

        print(f"MIDI Volume Controller")
        print(f"======================")
        print(f"MIDI Port: {ports[port_index]}")
        print(f"MIDI Channel: {self.midi_channel + 1}")
        print(f"CC Number: {self.cc_number}")
        print(f"Invert: {self.invert}")
        print()

        # Show current volume
        current = get_macos_volume()
        if current is not None:
            print(f"Current system volume: {current}%")

        print()
        print("Listening for MIDI... Press Ctrl+C to stop")

        return True

    def stop(self):
        """Stop listening."""
        self.running = False
        if self.debounce_timer:
            self.debounce_timer.cancel()
            self.debounce_timer = None
        if self.midi_in:
            self.midi_in.close_port()
            self.midi_in = None

    def apply_volume(self, volume):
        """Actually apply the volume change."""
        if volume != self.last_volume:
            self.last_volume = volume
            set_macos_volume(volume)
            print(f"Volume: {volume}%")

    def apply_pending_volume(self):
        """Apply pending volume (called by debounce timer)."""
        if self.pending_volume is not None:
            self.apply_volume(self.pending_volume)
            self.pending_volume = None
        self.debounce_timer = None

    def midi_callback(self, event, data=None):
        """Handle incoming MIDI messages."""
        message, delta_time = event

        if len(message) < 3:
            return

        status = message[0]
        cc = message[1]
        value = message[2]

        # Check if it's a CC message (0xB0-0xBF)
        if not (0xB0 <= status <= 0xBF):
            return

        # Check channel
        msg_channel = status & 0x0F
        if msg_channel != self.midi_channel:
            return

        # Check CC number
        if cc != self.cc_number:
            return

        # Convert MIDI value (0-127) to volume (0-100)
        if self.invert:
            value = 127 - value

        volume = int(value * 100 / 127)

        # Debounce: only apply at most every debounce_ms
        now = time.time() * 1000
        time_since_last = now - self.last_update_time

        if time_since_last >= self.debounce_ms:
            # Enough time has passed, apply immediately
            self.last_update_time = now
            self.apply_volume(volume)
            # Cancel any pending timer
            if self.debounce_timer:
                self.debounce_timer.cancel()
                self.debounce_timer = None
        else:
            # Too soon, store as pending and schedule timer
            self.pending_volume = volume
            if not self.debounce_timer:
                delay = (self.debounce_ms - time_since_last) / 1000.0
                self.debounce_timer = threading.Timer(delay, self.apply_pending_volume)
                self.debounce_timer.start()


def main():
    parser = argparse.ArgumentParser(
        description='Control macOS system volume via MIDI CC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  List MIDI ports:
    python midi2volume.py --list-midi

  Listen on IAC Driver, channel 1, CC 7 (standard volume):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7

  Listen with inverted values (127 = mute, 0 = full):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --invert
"""
    )

    parser.add_argument('--list-midi', action='store_true',
                        help='List available MIDI input ports')
    parser.add_argument('-m', '--midi-port', type=str,
                        help='MIDI input port (name or index)')
    parser.add_argument('-c', '--channel', type=int, default=1,
                        help='MIDI channel (1-16, default: 1)')
    parser.add_argument('--cc', type=int, default=7,
                        help='CC number to listen for (default: 7, standard volume)')
    parser.add_argument('--invert', action='store_true',
                        help='Invert CC values (127=mute, 0=full)')
    parser.add_argument('--debounce', type=int, default=100,
                        help='Debounce interval in ms (default: 100, max 10 updates/sec)')

    args = parser.parse_args()

    if args.list_midi:
        list_midi_ports()
        return

    if not args.midi_port:
        parser.error("--midi-port is required (or use --list-midi to see available ports)")

    controller = MidiVolumeController(
        midi_port=args.midi_port,
        midi_channel=args.channel,
        cc_number=args.cc,
        invert=args.invert,
        debounce_ms=args.debounce
    )

    if not controller.start():
        sys.exit(1)

    try:
        while controller.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        controller.stop()


if __name__ == '__main__':
    main()
