#!/usr/bin/env python3
"""
Audio Level to MIDI CC Converter

Converts audio input levels to MIDI CC messages for use with the Faderbank VU meters.

Usage:
    python audio2midi.py --list-audio          # List audio devices
    python audio2midi.py --list-midi           # List MIDI outputs
    python audio2midi.py -a DEVICE -c 0:1,1:2  # Map audio ch 0->CC 1, ch 1->CC 2

Example:
    python audio2midi.py -a "Built-in Microphone" -m "IAC Driver Bus 1" -c 0:1,1:2 --midi-channel 1
"""

import argparse
import signal
import sys
import time
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("Error: sounddevice not installed. Run: pip install sounddevice")
    sys.exit(1)

try:
    import rtmidi
except ImportError:
    print("Error: python-rtmidi not installed. Run: pip install python-rtmidi")
    sys.exit(1)


class AudioToMidi:
    def __init__(self, audio_device, midi_port, channel_mappings, midi_channel=1,
                 sample_rate=44100, block_size=1024, peak_hold_ms=100,
                 attack_ms=10, release_ms=300, avg_window=8):
        self.audio_device = audio_device
        self.midi_port = midi_port
        self.channel_mappings = channel_mappings  # {audio_ch: cc_number}
        self.midi_channel = midi_channel - 1  # Convert to 0-indexed
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.peak_hold_ms = peak_hold_ms
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.avg_window = avg_window  # Number of RMS readings to average

        self.running = False
        self.midi_out = None
        self.stream = None

        # State per channel
        self.smoothed_levels = {}
        self.peak_levels = {}
        self.peak_times = {}
        self.last_cc_values = {}
        self.rms_buffers = {}  # Running average buffers

        for ch in channel_mappings.keys():
            self.smoothed_levels[ch] = 0.0
            self.peak_levels[ch] = 0.0
            self.peak_times[ch] = 0
            self.last_cc_values[ch] = -1
            self.rms_buffers[ch] = []  # Circular buffer for RMS averaging

    def start(self):
        # Initialize MIDI output
        self.midi_out = rtmidi.MidiOut()
        available_ports = self.midi_out.get_ports()

        port_index = None
        for i, port in enumerate(available_ports):
            if self.midi_port.lower() in port.lower():
                port_index = i
                break

        if port_index is None:
            print(f"Error: MIDI port '{self.midi_port}' not found.")
            print("Available ports:", available_ports)
            sys.exit(1)

        self.midi_out.open_port(port_index)
        print(f"Opened MIDI port: {available_ports[port_index]}")

        # Find audio device
        devices = sd.query_devices()
        device_index = None

        if isinstance(self.audio_device, int):
            device_index = self.audio_device
        else:
            for i, dev in enumerate(devices):
                if self.audio_device.lower() in dev['name'].lower():
                    if dev['max_input_channels'] > 0:
                        device_index = i
                        break

        if device_index is None:
            print(f"Error: Audio device '{self.audio_device}' not found.")
            print("Use --list-audio to see available devices.")
            sys.exit(1)

        device_info = devices[device_index]
        print(f"Opened audio device: {device_info['name']}")

        # Determine channels needed
        max_channel = max(self.channel_mappings.keys()) + 1
        if max_channel > device_info['max_input_channels']:
            print(f"Error: Device only has {device_info['max_input_channels']} input channels")
            sys.exit(1)

        self.running = True

        # Start audio stream
        self.stream = sd.InputStream(
            device=device_index,
            channels=max_channel,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            callback=self.audio_callback
        )
        self.stream.start()

        print(f"Mapping: {', '.join(f'ch{ch}->CC{cc}' for ch, cc in self.channel_mappings.items())}")
        print(f"MIDI Channel: {self.midi_channel + 1}")
        print("Running... Press Ctrl+C to stop")

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")

        now = time.time() * 1000  # ms

        for audio_ch, cc_num in self.channel_mappings.items():
            if audio_ch >= indata.shape[1]:
                continue

            # Calculate RMS level for this channel
            channel_data = indata[:, audio_ch]
            rms = np.sqrt(np.mean(channel_data ** 2))

            # Add to running average buffer
            self.rms_buffers[audio_ch].append(rms)
            if len(self.rms_buffers[audio_ch]) > self.avg_window:
                self.rms_buffers[audio_ch].pop(0)

            # Use averaged RMS for smoother output
            avg_rms = np.mean(self.rms_buffers[audio_ch])

            # Convert to dB and then to 0-1 range
            # Assuming -60dB to 0dB range
            if avg_rms > 0:
                db = 20 * np.log10(avg_rms)
            else:
                db = -60

            # Normalize to 0-1 (map -60dB..0dB to 0..1)
            level = max(0, min(1, (db + 60) / 60))

            # Time-based smoothing (proper VU ballistics)
            # Calculate time since last update for this channel
            last_time = getattr(self, '_last_update_times', {}).get(audio_ch, now)
            if not hasattr(self, '_last_update_times'):
                self._last_update_times = {}
            delta_ms = now - last_time
            self._last_update_times[audio_ch] = now

            # Use configured attack and release times
            attack_ms = self.attack_ms
            release_ms = self.release_ms

            current = self.smoothed_levels[audio_ch]

            if level > current:
                # Attack - fast rise
                attack_coef = 1 - np.exp(-delta_ms / attack_ms)
                self.smoothed_levels[audio_ch] = current + (level - current) * attack_coef
            else:
                # Release - slow fall
                release_coef = np.exp(-delta_ms / release_ms)
                self.smoothed_levels[audio_ch] = level + (current - level) * release_coef

            # Peak hold
            if self.smoothed_levels[audio_ch] >= self.peak_levels[audio_ch]:
                self.peak_levels[audio_ch] = self.smoothed_levels[audio_ch]
                self.peak_times[audio_ch] = now
            elif now - self.peak_times[audio_ch] > self.peak_hold_ms:
                # Gradually fall from peak instead of snapping
                fall_coef = np.exp(-(now - self.peak_times[audio_ch] - self.peak_hold_ms) / release_ms)
                self.peak_levels[audio_ch] = self.smoothed_levels[audio_ch] + \
                    (self.peak_levels[audio_ch] - self.smoothed_levels[audio_ch]) * fall_coef

            # Convert to MIDI CC value
            cc_value = int(self.peak_levels[audio_ch] * 127)
            cc_value = max(0, min(127, cc_value))

            # Only send if changed
            if cc_value != self.last_cc_values[audio_ch]:
                self.send_cc(cc_num, cc_value)
                self.last_cc_values[audio_ch] = cc_value

    def send_cc(self, cc_number, value):
        # MIDI CC message: [0xB0 + channel, cc_number, value]
        message = [0xB0 + self.midi_channel, cc_number, value]
        self.midi_out.send_message(message)

    def stop(self):
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self.midi_out:
            self.midi_out.close_port()
        print("\nStopped.")


def list_audio_devices():
    print("Audio Input Devices:")
    print("-" * 60)
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            print(f"  [{i}] {dev['name']}")
            print(f"       Inputs: {dev['max_input_channels']}, Sample Rate: {dev['default_samplerate']}")


def list_midi_ports():
    print("MIDI Output Ports:")
    print("-" * 60)
    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    for i, port in enumerate(ports):
        print(f"  [{i}] {port}")
    if not ports:
        print("  No MIDI output ports found.")
        print("  On macOS, enable IAC Driver in Audio MIDI Setup.")


def parse_channel_mappings(mapping_str):
    """Parse channel mappings like '0:1,1:2,2:3' into {0: 1, 1: 2, 2: 3}"""
    mappings = {}
    for pair in mapping_str.split(','):
        parts = pair.strip().split(':')
        if len(parts) != 2:
            raise ValueError(f"Invalid mapping: {pair}")
        audio_ch = int(parts[0])
        cc_num = int(parts[1])
        mappings[audio_ch] = cc_num
    return mappings


def main():
    parser = argparse.ArgumentParser(
        description='Convert audio input levels to MIDI CC for Faderbank VU meters',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list-audio                    List audio input devices
  %(prog)s --list-midi                     List MIDI output ports
  %(prog)s -a 0 -m "IAC" -c 0:1            Map audio ch 0 to CC 1
  %(prog)s -a "Mic" -m "IAC" -c 0:1,1:2    Map ch 0->CC1, ch 1->CC2
        """
    )

    parser.add_argument('--list-audio', action='store_true',
                        help='List available audio input devices')
    parser.add_argument('--list-midi', action='store_true',
                        help='List available MIDI output ports')
    parser.add_argument('-a', '--audio-device', type=str,
                        help='Audio input device (name or index)')
    parser.add_argument('-m', '--midi-port', type=str,
                        help='MIDI output port name (partial match OK)')
    parser.add_argument('-c', '--channels', type=str,
                        help='Channel mappings as audio_ch:cc_num,... (e.g., 0:1,1:2)')
    parser.add_argument('--midi-channel', type=int, default=1,
                        help='MIDI channel (1-16, default: 1)')
    parser.add_argument('--sample-rate', type=int, default=44100,
                        help='Audio sample rate (default: 44100)')
    parser.add_argument('--block-size', type=int, default=1024,
                        help='Audio block size (default: 1024)')
    parser.add_argument('--peak-hold', type=int, default=100,
                        help='Peak hold time in ms (default: 100)')
    parser.add_argument('--attack', type=int, default=10,
                        help='Attack time in ms (default: 10)')
    parser.add_argument('--release', type=int, default=300,
                        help='Release time in ms (default: 300)')
    parser.add_argument('--avg-window', type=int, default=8,
                        help='RMS averaging window size (default: 8 blocks)')

    args = parser.parse_args()

    if args.list_audio:
        list_audio_devices()
        return

    if args.list_midi:
        list_midi_ports()
        return

    if not args.audio_device:
        parser.error("--audio-device (-a) is required")
    if not args.midi_port:
        parser.error("--midi-port (-m) is required")
    if not args.channels:
        parser.error("--channels (-c) is required")

    try:
        channel_mappings = parse_channel_mappings(args.channels)
    except ValueError as e:
        parser.error(str(e))

    # Try to parse audio device as int
    try:
        audio_device = int(args.audio_device)
    except ValueError:
        audio_device = args.audio_device

    converter = AudioToMidi(
        audio_device=audio_device,
        midi_port=args.midi_port,
        channel_mappings=channel_mappings,
        midi_channel=args.midi_channel,
        sample_rate=args.sample_rate,
        block_size=args.block_size,
        peak_hold_ms=args.peak_hold,
        attack_ms=args.attack,
        release_ms=args.release,
        avg_window=args.avg_window
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        converter.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    converter.start()

    # Keep running
    while converter.running:
        time.sleep(0.1)


if __name__ == '__main__':
    main()
