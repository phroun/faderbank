#!/usr/bin/env python3
"""
MIDI CC to Mouse Click Converter

Listens for a MIDI CC message and triggers a mouse click in the center of the screen,
then moves the cursor to the far right edge. Useful for pausing/unpausing video players.

Usage:
    python midi2click.py --list              # List MIDI input ports
    python midi2click.py -m "IAC" --cc 64    # Click on CC 64 from IAC port

Requirements:
    pip install mido python-rtmidi pyobjc-framework-Quartz
"""

import argparse
import signal
import sys
import time

try:
    import mido
except ImportError:
    print("Error: mido not installed. Run: pip install mido python-rtmidi")
    sys.exit(1)

try:
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPost,
        CGMainDisplayID,
        CGDisplayBounds,
        kCGEventMouseMoved,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGMouseButtonLeft,
        kCGHIDEventTap
    )
except ImportError:
    print("Error: pyobjc-framework-Quartz not installed. Run: pip install pyobjc-framework-Quartz")
    sys.exit(1)


class MidiToClick:
    def __init__(self, midi_port, cc_number, midi_channel=1, threshold=64, debug=False):
        self.midi_port = midi_port
        self.cc_number = cc_number
        self.midi_channel = midi_channel - 1  # 0-indexed
        self.threshold = threshold
        self.debug = debug

        self.running = False
        self.midi_in = None
        self.last_cc_value = 0

    def get_screen_size(self):
        """Get the main display dimensions."""
        main_display = CGMainDisplayID()
        bounds = CGDisplayBounds(main_display)
        return int(bounds.size.width), int(bounds.size.height)

    def move_mouse(self, x, y):
        """Move the mouse cursor to the specified position."""
        event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x, y), kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, event)

    def click_mouse(self, x, y):
        """Click at the specified position."""
        # Mouse down
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (x, y), kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, event)

        # Small delay
        time.sleep(0.05)

        # Mouse up
        event = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (x, y), kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, event)

    def perform_click_action(self):
        """Click center screen, then move cursor to far right."""
        width, height = self.get_screen_size()

        # Click in the center
        center_x = width // 2
        center_y = height // 2

        if self.debug:
            print(f"  Clicking at center ({center_x}, {center_y})")

        self.click_mouse(center_x, center_y)

        # Small delay before moving
        time.sleep(0.1)

        # Move to far right edge (middle height)
        right_x = width - 1
        right_y = height // 2

        if self.debug:
            print(f"  Moving cursor to right edge ({right_x}, {right_y})")

        self.move_mouse(right_x, right_y)

    def start(self):
        """Start listening for MIDI."""
        # Find matching input port
        available_ports = mido.get_input_names()
        port_name = None

        for name in available_ports:
            if self.midi_port.lower() in name.lower():
                port_name = name
                break

        if port_name is None:
            print(f"Error: MIDI input port '{self.midi_port}' not found.")
            print("Available input ports:")
            list_midi_ports()
            return False

        try:
            self.midi_in = mido.open_input(port_name)
        except Exception as e:
            print(f"Error opening MIDI port: {e}")
            return False

        self.running = True

        width, height = self.get_screen_size()

        print(f"MIDI Input: {port_name}")
        print(f"Listening for: CC {self.cc_number} on channel {self.midi_channel + 1}")
        print(f"Threshold: {self.threshold} (triggers on rise above)")
        print(f"Screen size: {width}x{height}")
        if self.debug:
            print("Debug mode: ON")
        print()
        print("Running... Press Ctrl+C to stop")

        return True

    def run(self):
        """Main loop to process MIDI messages."""
        while self.running:
            # Non-blocking read with timeout
            for msg in self.midi_in.iter_pending():
                if msg.type == 'control_change':
                    channel = msg.channel
                    cc_num = msg.control
                    cc_val = msg.value

                    if self.debug:
                        print(f"CC: ch={channel+1} cc={cc_num} val={cc_val}")

                    # Check if this is our target CC on our channel
                    if channel == self.midi_channel and cc_num == self.cc_number:
                        # Trigger on rising edge above threshold (only on positive values)
                        if cc_val > 0 and cc_val >= self.threshold and self.last_cc_value < self.threshold:
                            print(f"Triggered! CC {cc_num} = {cc_val}")
                            self.perform_click_action()

                        self.last_cc_value = cc_val

            time.sleep(0.001)  # Small delay to prevent CPU spinning

    def stop(self):
        """Stop and cleanup."""
        self.running = False
        if self.midi_in:
            self.midi_in.close()
        print("\nStopped.")


def list_midi_ports():
    """List available MIDI input ports."""
    print("MIDI Input Ports:")
    print("-" * 40)
    ports = mido.get_input_names()
    if ports:
        for i, name in enumerate(ports):
            print(f"  [{i}] {name}")
    else:
        print("  No MIDI input ports found.")


def main():
    parser = argparse.ArgumentParser(
        description='Convert MIDI CC to mouse click (center screen, then move to right edge)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                    List MIDI input ports
  %(prog)s -m "IAC" --cc 64          Trigger on CC 64 from IAC
  %(prog)s -m "IAC" --cc 64 --threshold 1   Trigger on any non-zero value

Use case:
  Pause/unpause videos by clicking center screen, then hide cursor at right edge.
        """
    )

    parser.add_argument('--list', action='store_true',
                        help='List available MIDI input ports')
    parser.add_argument('-m', '--midi-port', type=str,
                        help='MIDI input port name (partial match OK)')
    parser.add_argument('--cc', type=int,
                        help='CC number to listen for')
    parser.add_argument('--midi-channel', type=int, default=1,
                        help='MIDI channel (1-16, default: 1)')
    parser.add_argument('--threshold', type=int, default=64,
                        help='CC value threshold to trigger click (default: 64)')
    parser.add_argument('--debug', action='store_true',
                        help='Show all incoming MIDI CC messages')

    args = parser.parse_args()

    if args.list:
        list_midi_ports()
        return

    if not args.midi_port:
        parser.error("--midi-port (-m) is required")
    if args.cc is None:
        parser.error("--cc is required")

    clicker = MidiToClick(
        midi_port=args.midi_port,
        cc_number=args.cc,
        midi_channel=args.midi_channel,
        threshold=args.threshold,
        debug=args.debug
    )

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        clicker.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if not clicker.start():
        sys.exit(1)

    clicker.run()


if __name__ == '__main__':
    main()
