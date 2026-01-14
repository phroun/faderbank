#!/usr/bin/env python3
"""
OBS Audio to MIDI CC Converter

Reads audio levels from OBS via WebSocket and converts them to MIDI CC messages.

Usage:
    python obs2midi.py --list-sources              # List OBS audio sources
    python obs2midi.py --list-midi                 # List MIDI outputs
    python obs2midi.py -s "Mic/Aux" -m "IAC" --cc 1   # Map source to CC 1
    python obs2midi.py -s "Desktop Audio:0,Mic/Aux:1" -m "IAC"  # Multiple sources

Requires:
    - OBS 28+ (has built-in WebSocket server)
    - Enable WebSocket in OBS: Tools -> WebSocket Server Settings
"""

import argparse
import signal
import sys
import time
import threading

try:
    import obsws_python as obs
except ImportError:
    print("Error: obsws-python not installed. Run: pip install obsws-python")
    sys.exit(1)

try:
    import pygame.midi
except ImportError:
    print("Error: pygame not installed. Run: pip install pygame")
    sys.exit(1)


class OBSToMidi:
    def __init__(self, host, port, password, source_mappings, midi_port, midi_channel=1, update_rate=10, debug=False):
        self.host = host
        self.port = port
        self.password = password
        self.source_mappings = source_mappings  # {source_name: cc_number}
        self.midi_port = midi_port
        self.midi_channel = midi_channel - 1  # Convert to 0-indexed
        self.update_interval = 1.0 / update_rate  # Time between MIDI updates
        self.debug = debug

        self.running = False
        self.client = None
        self.events = None
        self.midi_out = None

        # Debouncing/averaging state
        self.level_buffers = {}  # source_name -> list of recent levels
        self.last_cc_values = {}  # source_name -> last sent CC value
        self.buffer_lock = threading.Lock()
        self.update_thread = None

    def start(self):
        """Start the OBS to MIDI bridge."""
        # Initialize MIDI output
        pygame.midi.init()

        port_index = None
        port_name = None
        for i in range(pygame.midi.get_count()):
            info = pygame.midi.get_device_info(i)
            name = info[1].decode('utf-8')
            is_output = info[3]
            if is_output and self.midi_port.lower() in name.lower():
                port_index = i
                port_name = name
                break

        if port_index is None:
            print(f"Error: MIDI port '{self.midi_port}' not found.")
            print("Available output ports:")
            for i in range(pygame.midi.get_count()):
                info = pygame.midi.get_device_info(i)
                if info[3]:
                    print(f"  {info[1].decode('utf-8')}")
            pygame.midi.quit()
            return False

        self.midi_out = pygame.midi.Output(port_index)
        print(f"Opened MIDI port: {port_name}")

        # Connect to OBS
        try:
            self.client = obs.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password if self.password else None,
                timeout=5
            )
            print(f"Connected to OBS at {self.host}:{self.port}")
        except Exception as e:
            print(f"Error connecting to OBS: {e}")
            print("\nMake sure:")
            print("  1. OBS is running")
            print("  2. WebSocket server is enabled (Tools -> WebSocket Server Settings)")
            print("  3. The host, port, and password are correct")
            pygame.midi.quit()
            return False

        # Connect event client for volume meters
        try:
            self.events = obs.EventClient(
                host=self.host,
                port=self.port,
                password=self.password if self.password else None
            )
            self.events.callback.register(self.on_input_volume_meters)
            print("Subscribed to audio level events")
        except Exception as e:
            print(f"Error setting up event listener: {e}")
            pygame.midi.quit()
            return False

        self.running = True

        # Initialize level buffers for each source
        for source_name in self.source_mappings.keys():
            self.level_buffers[source_name] = []

        # Start the periodic update thread
        self.update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.update_thread.start()

        print()
        print(f"Source -> CC Mappings:")
        for source, cc in self.source_mappings.items():
            print(f"  {source} -> CC {cc}")
        print(f"MIDI Channel: {self.midi_channel + 1}")
        print(f"Update Rate: {int(1.0 / self.update_interval)} Hz")
        if self.debug:
            print("Debug mode: ON")
        print()
        print("Running... Press Ctrl+C to stop")

        return True

    def _update_loop(self):
        """Periodic loop that averages buffered levels and sends MIDI."""
        while self.running:
            time.sleep(self.update_interval)

            with self.buffer_lock:
                for source_name, cc_num in self.source_mappings.items():
                    buffer = self.level_buffers.get(source_name, [])

                    if not buffer:
                        continue

                    # Use peak of buffered values (more responsive than average for VU)
                    peak_level = max(buffer)
                    self.level_buffers[source_name] = []  # Clear buffer

                    # Convert to MIDI CC (0-127)
                    level_clamped = max(0, min(1, peak_level))
                    cc_value = int(level_clamped * 127)

                    # Only send if changed
                    if self.last_cc_values.get(source_name) != cc_value:
                        self.last_cc_values[source_name] = cc_value
                        self.send_cc(cc_num, cc_value)
                        if self.debug:
                            print(f"  {source_name}: level={peak_level:.3f} -> CC{cc_num}={cc_value}")

    def on_input_volume_meters(self, data):
        """Handle incoming volume meter data from OBS."""
        if not self.running:
            return

        # data.inputs is a list of {inputName, inputLevelsMul}
        # inputLevelsMul is [[left_peak, left_rms, ...], [right_peak, right_rms, ...], ...]
        for input_data in data.inputs:
            source_name = input_data.get('inputName')
            levels = input_data.get('inputLevelsMul', [])

            if source_name not in self.source_mappings:
                continue

            if not levels:
                continue

            # Get peak level across all channels (usually stereo)
            # Each channel has [peak, peak_hold, input_peak] typically
            max_level = 0
            for channel_levels in levels:
                if channel_levels:
                    # Use the first value (usually the current peak)
                    peak = channel_levels[0] if channel_levels else 0
                    max_level = max(max_level, peak)

            # Buffer the level for averaging/debouncing
            with self.buffer_lock:
                if source_name in self.level_buffers:
                    self.level_buffers[source_name].append(max_level)

    def send_cc(self, cc_number, value):
        """Send MIDI CC message."""
        status = 0xB0 + self.midi_channel
        self.midi_out.write_short(status, cc_number, value)

    def stop(self):
        """Stop the bridge."""
        self.running = False
        if self.events:
            try:
                self.events.disconnect()
            except:
                pass
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
        if self.midi_out:
            self.midi_out.close()
        pygame.midi.quit()
        print("\nStopped.")

    def run(self):
        """Main loop - just keep alive while events come in."""
        while self.running:
            time.sleep(0.1)


def list_midi_ports():
    """List available MIDI output ports."""
    pygame.midi.init()

    print("Available MIDI output ports:")
    print("-" * 60)
    found = False
    for i in range(pygame.midi.get_count()):
        info = pygame.midi.get_device_info(i)
        name = info[1].decode('utf-8')
        is_output = info[3]
        if is_output:
            print(f"  [{i}] {name}")
            found = True

    if not found:
        print("  (none found)")
        print("  On macOS, enable IAC Driver in Audio MIDI Setup.")

    pygame.midi.quit()


def list_obs_sources(host, port, password):
    """List available audio sources in OBS."""
    try:
        client = obs.ReqClient(
            host=host,
            port=port,
            password=password if password else None,
            timeout=5
        )
    except Exception as e:
        print(f"Error connecting to OBS: {e}")
        print("\nMake sure:")
        print("  1. OBS is running")
        print("  2. WebSocket server is enabled (Tools -> WebSocket Server Settings)")
        return

    print("Available OBS audio sources:")
    print("-" * 60)

    try:
        # Get all inputs
        response = client.get_input_list()
        inputs = response.inputs

        audio_inputs = []
        for inp in inputs:
            # Check if it has audio capabilities by trying to get its volume
            try:
                vol = client.get_input_volume(inp['inputName'])
                audio_inputs.append(inp['inputName'])
            except:
                pass

        if audio_inputs:
            for name in audio_inputs:
                print(f"  {name}")
        else:
            print("  (no audio sources found)")

        # Also list special audio sources
        print()
        print("Special audio sources (may also work):")
        print("  Desktop Audio")
        print("  Desktop Audio 2")
        print("  Mic/Aux")
        print("  Mic/Aux 2")

    except Exception as e:
        print(f"Error listing sources: {e}")
    finally:
        client.disconnect()


def parse_source_mappings(mapping_str):
    """Parse source mappings like 'Desktop Audio:1,Mic/Aux:2'"""
    mappings = {}
    for pair in mapping_str.split(','):
        pair = pair.strip()
        if ':' in pair:
            # Format: "Source Name:CC"
            parts = pair.rsplit(':', 1)  # rsplit to handle colons in source names
            source = parts[0].strip()
            cc = int(parts[1].strip())
            mappings[source] = cc
        else:
            raise ValueError(f"Invalid mapping format: {pair} (expected 'Source:CC')")
    return mappings


def main():
    parser = argparse.ArgumentParser(
        description='Convert OBS audio levels to MIDI CC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  List OBS audio sources:
    python obs2midi.py --list-sources

  List MIDI ports:
    python obs2midi.py --list-midi

  Map single source to CC:
    python obs2midi.py -s "Desktop Audio" -m "IAC" --cc 1

  Map multiple sources:
    python obs2midi.py -s "Desktop Audio:1,Mic/Aux:2" -m "IAC"

OBS WebSocket Setup:
  1. In OBS, go to Tools -> WebSocket Server Settings
  2. Enable the WebSocket server
  3. Note the port (default 4455) and password if set
"""
    )

    parser.add_argument('--list-sources', action='store_true',
                        help='List available OBS audio sources')
    parser.add_argument('--list-midi', action='store_true',
                        help='List available MIDI output ports')
    parser.add_argument('-s', '--sources', type=str,
                        help='Source mappings (e.g., "Desktop Audio:1,Mic/Aux:2" or just source name with --cc)')
    parser.add_argument('--cc', type=int,
                        help='CC number for single source mode')
    parser.add_argument('-m', '--midi-port', type=str,
                        help='MIDI output port name (partial match OK)')
    parser.add_argument('--midi-channel', type=int, default=1,
                        help='MIDI channel (1-16, default: 1)')
    parser.add_argument('--host', type=str, default='localhost',
                        help='OBS WebSocket host (default: localhost)')
    parser.add_argument('--port', type=int, default=4455,
                        help='OBS WebSocket port (default: 4455)')
    parser.add_argument('--password', type=str, default='',
                        help='OBS WebSocket password (if set)')
    parser.add_argument('--debug', action='store_true',
                        help='Show debug output when CC values are sent')

    args = parser.parse_args()

    if args.list_midi:
        list_midi_ports()
        return

    if args.list_sources:
        list_obs_sources(args.host, args.port, args.password)
        return

    if not args.sources:
        parser.error("--sources (-s) is required")
    if not args.midi_port:
        parser.error("--midi-port (-m) is required")

    # Parse source mappings
    try:
        if ':' in args.sources:
            # Multiple mappings format: "Source1:CC1,Source2:CC2"
            source_mappings = parse_source_mappings(args.sources)
        else:
            # Single source mode: requires --cc
            if not args.cc:
                parser.error("--cc is required when using single source without mapping")
            source_mappings = {args.sources: args.cc}
    except ValueError as e:
        parser.error(str(e))

    bridge = OBSToMidi(
        host=args.host,
        port=args.port,
        password=args.password,
        source_mappings=source_mappings,
        midi_port=args.midi_port,
        midi_channel=args.midi_channel,
        debug=args.debug
    )

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if not bridge.start():
        sys.exit(1)

    bridge.run()


if __name__ == '__main__':
    main()
