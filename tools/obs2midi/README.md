# OBS to MIDI

Reads audio levels from OBS via WebSocket and converts them to MIDI CC messages for use with the Faderbank VU meters.

## Requirements

- OBS 28+ (has built-in WebSocket server)
- Python 3.9+

## Setup

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Enable OBS WebSocket:
   - In OBS, go to **Tools → WebSocket Server Settings**
   - Check "Enable WebSocket server"
   - Note the port (default: 4455)
   - Set a password if desired

## Usage

List available OBS audio sources:
```bash
python obs2midi.py --list-sources
```

List available MIDI output ports:
```bash
python obs2midi.py --list-midi
```

Map a single source to a CC:
```bash
python obs2midi.py -s "Desktop Audio" -m "IAC" --cc 1
```

Map multiple sources:
```bash
python obs2midi.py -s "Desktop Audio:1,Mic/Aux:2" -m "IAC"
```

With password:
```bash
python obs2midi.py -s "Desktop Audio:1" -m "IAC" --password "your_password"
```

## Options

- `-s, --sources`: Source name(s) with CC mappings
- `--cc`: CC number (for single source mode)
- `-m, --midi-port`: MIDI output port (partial name match)
- `--midi-channel`: MIDI channel 1-16 (default: 1)
- `--host`: OBS WebSocket host (default: localhost)
- `--port`: OBS WebSocket port (default: 4455)
- `--password`: OBS WebSocket password (if set)

## Common Audio Sources

- `Desktop Audio` - System audio output
- `Desktop Audio 2` - Secondary audio output
- `Mic/Aux` - Default microphone
- `Mic/Aux 2` - Secondary microphone
- Any audio input sources you've added in OBS
