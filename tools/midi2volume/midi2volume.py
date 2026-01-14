#!/usr/bin/env python3
"""
MIDI to macOS System Volume Controller

Listens to MIDI CC messages and controls macOS system volume.
Can target a specific audio output device.

Usage:
    python midi2volume.py -m "IAC Driver Bus 1" -c 1 --cc 7
    python midi2volume.py --list-midi
    python midi2volume.py --list-audio
    python midi2volume.py -m "IAC" -c 1 --cc 7 --audio-device "External Headphones"
"""

import argparse
import subprocess
import sys
import time
import threading

try:
    import pygame.midi
except ImportError:
    print("Error: pygame not installed. Run: pip install pygame")
    sys.exit(1)


def list_midi_ports():
    """List available MIDI input ports."""
    pygame.midi.init()

    print("Available MIDI input ports:")
    count = pygame.midi.get_count()
    found = False
    for i in range(count):
        info = pygame.midi.get_device_info(i)
        # info: (interface, name, is_input, is_output, opened)
        name = info[1].decode('utf-8')
        is_input = info[2]
        if is_input:
            found = True
            print(f"  {i}: {name}")

    if not found:
        print("  (none found)")

    pygame.midi.quit()


def list_audio_devices():
    """List available audio output devices using SwitchAudioSource."""
    try:
        result = subprocess.run(
            ['SwitchAudioSource', '-a', '-t', 'output'],
            check=True,
            capture_output=True,
            text=True
        )
        print("Available audio output devices:")
        for line in result.stdout.strip().split('\n'):
            if line:
                print(f"  {line}")

        # Show current device
        current = subprocess.run(
            ['SwitchAudioSource', '-c'],
            capture_output=True,
            text=True
        )
        if current.returncode == 0:
            print(f"\nCurrent output device: {current.stdout.strip()}")

    except FileNotFoundError:
        print("Error: SwitchAudioSource not found.")
        print("Install it with: brew install switchaudio-osx")
        print("\nAlternatively, you can list devices with:")
        print("  system_profiler SPAudioDataType")
        sys.exit(1)


def get_current_audio_device():
    """Get the current audio output device name."""
    try:
        result = subprocess.run(
            ['SwitchAudioSource', '-c'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def set_audio_device(device_name):
    """Set the audio output device."""
    try:
        result = subprocess.run(
            ['SwitchAudioSource', '-s', device_name],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        print("Error: SwitchAudioSource not found.")
        print("Install it with: brew install switchaudio-osx")
        return False


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
    def __init__(self, midi_port, midi_channel, cc_number, invert=False, debounce_ms=100, audio_device=None):
        self.midi_port = midi_port
        self.midi_channel = midi_channel - 1  # Convert to 0-indexed
        self.cc_number = cc_number
        self.invert = invert
        self.debounce_ms = debounce_ms
        self.audio_device = audio_device
        self.original_device = None  # To restore when done
        self.running = False
        self.midi_in = None
        self.last_volume = None
        self.pending_volume = None
        self.last_update_time = 0
        self.debounce_timer = None

    def start(self):
        """Start listening for MIDI messages."""
        pygame.midi.init()

        count = pygame.midi.get_count()

        # Find the port
        port_id = None
        port_name = None

        if isinstance(self.midi_port, int):
            if self.midi_port < count:
                info = pygame.midi.get_device_info(self.midi_port)
                if info[2]:  # is_input
                    port_id = self.midi_port
                    port_name = info[1].decode('utf-8')
        else:
            for i in range(count):
                info = pygame.midi.get_device_info(i)
                name = info[1].decode('utf-8')
                is_input = info[2]
                if is_input and self.midi_port.lower() in name.lower():
                    port_id = i
                    port_name = name
                    break

        if port_id is None:
            print(f"Error: MIDI port '{self.midi_port}' not found.")
            print("Available input ports:")
            for i in range(count):
                info = pygame.midi.get_device_info(i)
                if info[2]:  # is_input
                    print(f"  {i}: {info[1].decode('utf-8')}")
            pygame.midi.quit()
            return False

        try:
            self.midi_in = pygame.midi.Input(port_id)
        except Exception as e:
            print(f"Error opening MIDI port: {e}")
            pygame.midi.quit()
            return False

        self.running = True

        # Handle audio device selection
        if self.audio_device:
            self.original_device = get_current_audio_device()
            if self.original_device:
                if not set_audio_device(self.audio_device):
                    print(f"Error: Could not switch to audio device '{self.audio_device}'")
                    pygame.midi.quit()
                    return False

        print(f"MIDI Volume Controller")
        print(f"======================")
        print(f"MIDI Port: {port_name}")
        print(f"MIDI Channel: {self.midi_channel + 1}")
        print(f"CC Number: {self.cc_number}")
        print(f"Invert: {self.invert}")
        if self.audio_device:
            print(f"Audio Device: {self.audio_device}")
        print()

        # Show current volume
        current = get_macos_volume()
        if current is not None:
            print(f"Current volume: {current}%")

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
            self.midi_in.close()
            self.midi_in = None
        # Restore original audio device if we changed it
        if self.original_device and self.audio_device:
            set_audio_device(self.original_device)
            print(f"Restored audio device: {self.original_device}")
        pygame.midi.quit()

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

    def process_event(self, event):
        """Process a MIDI event."""
        # event: [[status, data1, data2, data3], timestamp]
        data = event[0]
        status = data[0]

        # Check if it's a CC message (0xB0-0xBF)
        if not (0xB0 <= status <= 0xBF):
            return

        # Check channel
        msg_channel = status & 0x0F
        if msg_channel != self.midi_channel:
            return

        cc = data[1]
        value = data[2]

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

    def run(self):
        """Main loop to process MIDI messages."""
        while self.running:
            if self.midi_in.poll():
                events = self.midi_in.read(10)
                for event in events:
                    self.process_event(event)
            time.sleep(0.001)  # Small sleep to avoid busy-waiting


def main():
    parser = argparse.ArgumentParser(
        description='Control macOS system volume via MIDI CC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  List MIDI ports:
    python midi2volume.py --list-midi

  List audio output devices:
    python midi2volume.py --list-audio

  Listen on IAC Driver, channel 1, CC 7 (standard volume):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7

  Control a specific audio device:
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --audio-device "External Headphones"

  Listen with inverted values (127 = mute, 0 = full):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --invert

Note: --audio-device requires SwitchAudioSource (brew install switchaudio-osx)
"""
    )

    parser.add_argument('--list-midi', action='store_true',
                        help='List available MIDI input ports')
    parser.add_argument('--list-audio', action='store_true',
                        help='List available audio output devices')
    parser.add_argument('-m', '--midi-port', type=str,
                        help='MIDI input port (name or index)')
    parser.add_argument('-c', '--channel', type=int, default=1,
                        help='MIDI channel (1-16, default: 1)')
    parser.add_argument('--cc', type=int, default=7,
                        help='CC number to listen for (default: 7, standard volume)')
    parser.add_argument('--audio-device', type=str,
                        help='Target audio output device (requires SwitchAudioSource)')
    parser.add_argument('--invert', action='store_true',
                        help='Invert CC values (127=mute, 0=full)')
    parser.add_argument('--debounce', type=int, default=100,
                        help='Debounce interval in ms (default: 100, max 10 updates/sec)')

    args = parser.parse_args()

    if args.list_midi:
        list_midi_ports()
        return

    if args.list_audio:
        list_audio_devices()
        return

    if not args.midi_port:
        parser.error("--midi-port is required (or use --list-midi to see available ports)")

    controller = MidiVolumeController(
        midi_port=args.midi_port,
        midi_channel=args.channel,
        cc_number=args.cc,
        invert=args.invert,
        debounce_ms=args.debounce,
        audio_device=args.audio_device
    )

    if not controller.start():
        sys.exit(1)

    try:
        controller.run()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        controller.stop()


if __name__ == '__main__':
    main()
