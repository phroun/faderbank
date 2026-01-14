# MIDI to macOS Volume Controller

Listens to MIDI CC messages and controls macOS system master volume.

## Installation

```bash
cd tools/midi2volume
pip install -r requirements.txt
```

## Usage

```bash
# List available MIDI ports
python midi2volume.py --list-midi

# Listen on IAC Driver, channel 1, CC 7 (standard volume CC)
python midi2volume.py -m "IAC Driver" -c 1 --cc 7

# With inverted values (127 = mute, 0 = full volume)
python midi2volume.py -m "IAC Driver" -c 1 --cc 7 --invert
```

## Arguments

| Argument | Description |
|----------|-------------|
| `--list-midi` | List available MIDI input ports |
| `-m`, `--midi-port` | MIDI input port (name or index) |
| `-c`, `--channel` | MIDI channel 1-16 (default: 1) |
| `--cc` | CC number to listen for (default: 7) |
| `--invert` | Invert CC values |

## Notes

- Only works on macOS (uses `osascript` to control volume)
- CC 7 is the standard MIDI volume controller
- MIDI values 0-127 are mapped to volume 0-100%
