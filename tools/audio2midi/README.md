# Audio to MIDI CC Converter

Converts audio input levels to MIDI CC messages for Faderbank VU meters.

## macOS Setup

### 1. Enable IAC Driver (Virtual MIDI)

1. Open **Applications > Utilities > Audio MIDI Setup**
2. Go to **Window > Show MIDI Studio** (or press Cmd+2)
3. Double-click **IAC Driver**
4. Check **"Device is online"**
5. You should see "IAC Driver Bus 1" in the list

### 2. Install Dependencies

```bash
cd tools/audio2midi
pip install -r requirements.txt
```

On macOS, you may also need PortAudio:
```bash
brew install portaudio
```

## Usage

### List available devices:

```bash
# List audio input devices
python audio2midi.py --list-audio

# List MIDI output ports
python audio2midi.py --list-midi
```

### Run the converter:

```bash
# Basic: Map audio channel 0 to MIDI CC 1
python audio2midi.py -a "Built-in Microphone" -m "IAC Driver" -c 0:1

# Multiple channels: Map ch0->CC1, ch1->CC2
python audio2midi.py -a "Audio Interface" -m "IAC Driver" -c 0:1,1:2

# With options
python audio2midi.py \
    -a "Scarlett 2i2" \
    -m "IAC Driver Bus 1" \
    -c 0:1,1:2 \
    --midi-channel 1 \
    --peak-hold 100 \
    --smoothing 0.3
```

### Parameters:

| Flag | Description | Default |
|------|-------------|---------|
| `-a`, `--audio-device` | Audio input device (name or index) | Required |
| `-m`, `--midi-port` | MIDI output port (partial match) | Required |
| `-c`, `--channels` | Mapping: `audio_ch:cc_num,...` | Required |
| `--midi-channel` | MIDI channel (1-16) | 1 |
| `--peak-hold` | Peak hold time in ms | 100 |
| `--smoothing` | Release smoothing (0-1) | 0.3 |
| `--sample-rate` | Audio sample rate | 44100 |
| `--block-size` | Audio block size | 1024 |

## Connecting to Faderbank

1. Run this tool to send MIDI CC from audio levels
2. In Faderbank, set each channel's **VU Input CC** to match your mapping
3. Connect your MIDI output (IAC Driver or interface) to Faderbank's MIDI input

## Example Setup

8-channel mixer sending VU levels:
```bash
python audio2midi.py \
    -a "USB Audio Interface" \
    -m "IAC Driver" \
    -c 0:1,1:2,2:3,3:4,4:5,5:6,6:7,7:8 \
    --midi-channel 1
```

This maps:
- Audio channel 0 → MIDI CC 1
- Audio channel 1 → MIDI CC 2
- ... and so on

In Faderbank, configure each channel strip's `midi_cc_vu_input` to 1, 2, 3, etc.
