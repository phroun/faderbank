#!/usr/bin/env python3
"""
MIDI to macOS System Volume Controller

Listens to MIDI CC messages and controls macOS audio device volume.
Can target a specific audio output device directly via CoreAudio,
even when it's part of a multi-output aggregate device.

Usage:
    python midi2volume.py -m "IAC Driver Bus 1" -c 1 --cc 7
    python midi2volume.py --list-midi
    python midi2volume.py --list-audio
    python midi2volume.py -m "IAC" -c 1 --cc 7 --audio-device "External Headphones"
"""

import argparse
import ctypes
import subprocess
import sys
import time
import threading

try:
    import pygame.midi
except ImportError:
    print("Error: pygame not installed. Run: pip install pygame")
    sys.exit(1)

# CoreAudio constants
kAudioHardwarePropertyDevices = 1684370979  # 'dev#'
kAudioObjectPropertyScopeGlobal = 1735159650  # 'glob'
kAudioObjectPropertyElementMain = 0
kAudioDevicePropertyScopeOutput = 1869968496  # 'outp'
kAudioObjectPropertyName = 1819173229  # 'lnam'
kAudioDevicePropertyVolumeScalar = 1987013741  # 'volm'
kAudioDevicePropertyMute = 1836414053  # 'mute'
kAudioHardwarePropertyDefaultOutputDevice = 1682929012  # 'dOut'
kAudioObjectSystemObject = 1

# Try to load CoreAudio framework
try:
    _coreaudio = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
    COREAUDIO_AVAILABLE = True
except OSError:
    COREAUDIO_AVAILABLE = False


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ('mSelector', ctypes.c_uint32),
        ('mScope', ctypes.c_uint32),
        ('mElement', ctypes.c_uint32),
    ]


def get_audio_devices():
    """Get list of audio output devices using CoreAudio."""
    if not COREAUDIO_AVAILABLE:
        return []

    # Get the size of the devices array
    prop_address = AudioObjectPropertyAddress(
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    )

    data_size = ctypes.c_uint32(0)
    status = _coreaudio.AudioObjectGetPropertyDataSize(
        kAudioObjectSystemObject,
        ctypes.byref(prop_address),
        0,
        None,
        ctypes.byref(data_size)
    )

    if status != 0:
        return []

    # Get device IDs
    num_devices = data_size.value // ctypes.sizeof(ctypes.c_uint32)
    device_ids = (ctypes.c_uint32 * num_devices)()

    status = _coreaudio.AudioObjectGetPropertyData(
        kAudioObjectSystemObject,
        ctypes.byref(prop_address),
        0,
        None,
        ctypes.byref(data_size),
        device_ids
    )

    if status != 0:
        return []

    devices = []
    for device_id in device_ids:
        name = get_device_name(device_id)
        if name and has_output_volume(device_id):
            devices.append({
                'id': device_id,
                'name': name,
                'has_volume': True
            })

    return devices


def get_device_name(device_id):
    """Get the name of an audio device."""
    if not COREAUDIO_AVAILABLE:
        return None

    prop_address = AudioObjectPropertyAddress(
        kAudioObjectPropertyName,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    )

    # CFStringRef
    cf_string = ctypes.c_void_p()
    data_size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))

    status = _coreaudio.AudioObjectGetPropertyData(
        device_id,
        ctypes.byref(prop_address),
        0,
        None,
        ctypes.byref(data_size),
        ctypes.byref(cf_string)
    )

    if status != 0 or not cf_string:
        return None

    # Convert CFString to Python string
    try:
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        buffer = ctypes.create_string_buffer(256)
        if cf.CFStringGetCString(cf_string, buffer, 256, 0x08000100):  # kCFStringEncodingUTF8
            name = buffer.value.decode('utf-8')
        else:
            name = None

        cf.CFRelease(cf_string)
        return name
    except Exception:
        return None


def has_output_volume(device_id):
    """Check if device has output volume control."""
    if not COREAUDIO_AVAILABLE:
        return False

    prop_address = AudioObjectPropertyAddress(
        kAudioDevicePropertyVolumeScalar,
        kAudioDevicePropertyScopeOutput,
        0  # Master channel
    )

    has_property = _coreaudio.AudioObjectHasProperty(device_id, ctypes.byref(prop_address))
    return bool(has_property)


def get_device_id_by_name(name):
    """Find a device ID by name (partial match)."""
    devices = get_audio_devices()
    for device in devices:
        if name.lower() in device['name'].lower():
            return device['id']
    return None


def get_device_volume(device_id):
    """Get volume of a specific device (0.0 to 1.0)."""
    if not COREAUDIO_AVAILABLE:
        return None

    prop_address = AudioObjectPropertyAddress(
        kAudioDevicePropertyVolumeScalar,
        kAudioDevicePropertyScopeOutput,
        0  # Master channel
    )

    volume = ctypes.c_float(0.0)
    data_size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_float))

    status = _coreaudio.AudioObjectGetPropertyData(
        device_id,
        ctypes.byref(prop_address),
        0,
        None,
        ctypes.byref(data_size),
        ctypes.byref(volume)
    )

    if status != 0:
        # Try channel 1 if master (0) doesn't work
        prop_address.mElement = 1
        status = _coreaudio.AudioObjectGetPropertyData(
            device_id,
            ctypes.byref(prop_address),
            0,
            None,
            ctypes.byref(data_size),
            ctypes.byref(volume)
        )

    if status != 0:
        return None

    return volume.value


def set_device_volume(device_id, volume):
    """Set volume of a specific device (0.0 to 1.0)."""
    if not COREAUDIO_AVAILABLE:
        return False

    volume = max(0.0, min(1.0, volume))

    prop_address = AudioObjectPropertyAddress(
        kAudioDevicePropertyVolumeScalar,
        kAudioDevicePropertyScopeOutput,
        0  # Master channel
    )

    volume_value = ctypes.c_float(volume)
    data_size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_float))

    # Check if property is settable
    is_settable = ctypes.c_uint32(0)
    _coreaudio.AudioObjectIsPropertySettable(
        device_id,
        ctypes.byref(prop_address),
        ctypes.byref(is_settable)
    )

    if not is_settable.value:
        # Try channel 1 if master isn't settable
        prop_address.mElement = 1
        _coreaudio.AudioObjectIsPropertySettable(
            device_id,
            ctypes.byref(prop_address),
            ctypes.byref(is_settable)
        )

    status = _coreaudio.AudioObjectSetPropertyData(
        device_id,
        ctypes.byref(prop_address),
        0,
        None,
        data_size,
        ctypes.byref(volume_value)
    )

    if status != 0 and prop_address.mElement == 0:
        # If master channel failed, try setting both L/R channels
        for channel in [1, 2]:
            prop_address.mElement = channel
            _coreaudio.AudioObjectSetPropertyData(
                device_id,
                ctypes.byref(prop_address),
                0,
                None,
                data_size,
                ctypes.byref(volume_value)
            )
        return True

    return status == 0


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
    """List available audio output devices with volume control."""
    if not COREAUDIO_AVAILABLE:
        print("Error: CoreAudio not available.")
        print("Falling back to SwitchAudioSource...")
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
        except FileNotFoundError:
            print("Error: SwitchAudioSource not found.")
            print("Install it with: brew install switchaudio-osx")
        return

    devices = get_audio_devices()

    print("Available audio output devices (with volume control):")
    print("-" * 60)

    if not devices:
        print("  (none found)")
        return

    for device in devices:
        volume = get_device_volume(device['id'])
        if volume is not None:
            vol_percent = int(volume * 100)
            print(f"  {device['name']}")
            print(f"       ID: {device['id']}, Volume: {vol_percent}%")
        else:
            print(f"  {device['name']}")
            print(f"       ID: {device['id']}, Volume: (not readable)")


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
        self.audio_device_id = None  # CoreAudio device ID
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
            if COREAUDIO_AVAILABLE:
                self.audio_device_id = get_device_id_by_name(self.audio_device)
                if self.audio_device_id is None:
                    print(f"Error: Audio device '{self.audio_device}' not found.")
                    print("Use --list-audio to see available devices.")
                    pygame.midi.quit()
                    return False
            else:
                print("Warning: CoreAudio not available, --audio-device will use system volume")

        print(f"MIDI Volume Controller")
        print(f"======================")
        print(f"MIDI Port: {port_name}")
        print(f"MIDI Channel: {self.midi_channel + 1}")
        print(f"CC Number: {self.cc_number}")
        print(f"Invert: {self.invert}")
        if self.audio_device:
            if self.audio_device_id:
                print(f"Audio Device: {self.audio_device} (CoreAudio ID: {self.audio_device_id})")
            else:
                print(f"Audio Device: {self.audio_device} (using system volume)")
        print()

        # Show current volume
        if self.audio_device_id:
            volume = get_device_volume(self.audio_device_id)
            if volume is not None:
                print(f"Current device volume: {int(volume * 100)}%")
        else:
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
            self.midi_in.close()
            self.midi_in = None
        pygame.midi.quit()

    def apply_volume(self, volume):
        """Actually apply the volume change."""
        if volume != self.last_volume:
            self.last_volume = volume

            if self.audio_device_id:
                # Use CoreAudio to set device volume directly
                set_device_volume(self.audio_device_id, volume / 100.0)
            else:
                # Use system volume
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
        description='Control macOS audio device volume via MIDI CC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  List MIDI ports:
    python midi2volume.py --list-midi

  List audio output devices:
    python midi2volume.py --list-audio

  Listen on IAC Driver, channel 1, CC 7 (standard volume):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7

  Control a specific audio device (works with multi-output devices):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --audio-device "External Headphones"

  Listen with inverted values (127 = mute, 0 = full):
    python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --invert

Note: --audio-device uses CoreAudio to control volume directly on the
specified device, even when it's part of a multi-output aggregate device.
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
                        help='Target audio output device (uses CoreAudio directly)')
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
