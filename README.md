# Screen Vision MCP Server

> Give Claude Code the ability to see your screen

Screen Vision is an MCP (Model Context Protocol) server that provides Claude with 13 tools for screen capture, phone camera integration, real-time screen watching with audio transcription, video analysis, and OCR. Built for both personal use and corporate environments with comprehensive security controls.

## Tools

### 1. `capture_screen`
Capture the full screen or a specific monitor with optional delay for window switching.

**Parameters:**
- `delay_seconds` (int, default: 3) — Wait before capturing (useful for switching windows)
- `monitor` (int, default: 0) — Monitor index (0 = all monitors, 1+ = specific monitor)
- `scale` (float, default: 0.5) — Scaling factor to reduce image size

**Returns:** JSON with base64 JPEG image, cursor position, active window, OCR text near cursor, and security metadata.

### 2. `capture_region`
Capture a specific rectangular region of the screen.

**Parameters:**
- `x` (int) — Left coordinate
- `y` (int) — Top coordinate
- `width` (int) — Region width
- `height` (int) — Region height
- `scale` (float, default: 1.0) — Scaling factor

**Returns:** JSON with captured region as base64 JPEG.

### 3. `capture_window`
Capture a specific window by title (e.g., "Chrome", "Terminal", "Slack").

**Parameters:**
- `window_title` (str) — Title of the window to capture
- `scale` (float, default: 0.5) — Scaling factor

**Returns:** JSON with captured window as base64 JPEG.

### 4. `list_monitors`
List all available monitors with their resolutions and positions.

**Returns:** JSON with monitor info (index, resolution, x/y position).

### 5. `watch_screen`
Watch the screen for a duration with intelligent frame sampling and optional audio transcription.

**Parameters:**
- `duration_seconds` (int, default: 60) — How long to watch
- `interval_seconds` (float, default: 4.0) — Time between frame captures
- `include_audio` (bool, default: true) — Whether to record and transcribe audio using Whisper
- `max_frames` (int, default: 30) — Maximum keyframes to keep (duplicates are skipped)

**Returns:** JSON with keyframes (base64 JPEG images + OCR near cursor), audio transcript with timestamps aligned to nearest frames, and watch metadata.

**Features:**
- Skips duplicate frames (content-based deduplication)
- Transcribes audio with Faster-Whisper (if `include_audio=true`)
- Aligns transcript segments to nearest frames
- OCR on each keyframe with text near cursor

### 6. `analyze_video`
Analyze a local video file by extracting keyframes at even intervals.

**Parameters:**
- `file_path` (str) — Path to video file (MP4, MOV, AVI, etc.)
- `start_time` (float, default: 0) — Start time in seconds
- `end_time` (float, optional) — End time in seconds (default: entire video)
- `max_frames` (int, default: 20) — Maximum frames to extract

**Returns:** JSON with keyframes (base64 JPEG + timestamp), video duration, and frames extracted.

### 7. `read_screen_text`
Run OCR on the screen or a specific region and extract text.

**Parameters:**
- `region` (str, optional) — Region as "x,y,width,height" (e.g., "100,200,800,600")

**Returns:** JSON with extracted text, average OCR confidence, and security metadata.

### 8. `get_active_context`
Get lightweight context without capturing a screenshot: active window, cursor position, and monitor info.

**Returns:** JSON with cursor position, active window (app name + title), and monitor list.

## Phone Camera Integration

Two approaches based on edition:

### Work Mode — File-Drop (AirDrop)
```
1. Take a photo on your phone
2. AirDrop to your Mac (or save to a shared folder)
3. Claude analyzes it:
   - "Analyze this photo" → Claude calls analyze_image()
```

New tool: `analyze_image(file_path, prompt)` — analyze any image file

### Personal Mode — Live Camera Stream
```
1. Claude shows QR code → show_pairing_qr()
2. Scan QR with iPhone → Safari opens camera page
3. Camera streams to Claude in real-time
4. "What am I looking at?" → Claude calls capture_camera()
5. "Watch me work on this circuit" → Claude calls watch_camera()
```

New tools:
- `show_pairing_qr()` — display QR code for phone pairing
- `capture_camera()` — grab latest phone camera frame
- `watch_camera()` — stream phone camera with audio
- `phone_status()` — check connection

### Setup for Personal Mode

```bash
# One-time: install mkcert for local TLS
brew install mkcert
mkcert -install
mkcert $(ipconfig getifaddr en0)  # generates certs for your LAN IP

# Trust on iPhone:
# 1. AirDrop rootCA.pem to phone
# 2. Settings → General → VPN & Device Management → Install
# 3. Settings → General → About → Certificate Trust Settings → Enable
```

## Quick Start

### Installation

```bash
# Install from GitLab
pip install git+ssh://git@gitlab.agodadev.io/partnertech/rta-ai/screen-vision.git[full]

# Or install locally
cd screen-vision
pip install -e ".[full]"

# Install system dependencies
brew install tesseract ffmpeg
```

**Dependencies:**
- **Tesseract** (required for OCR): `brew install tesseract`
- **FFmpeg** (required for video/audio): `brew install ffmpeg`

### MCP Setup

Add to your `.mcp.json` (or Claude Code settings):

```json
{
  "mcpServers": {
    "screen-vision": {
      "command": "screen-vision-mcp",
      "args": []
    }
  }
}
```

Or configure via Claude Code UI:
1. Open Settings → MCP Servers
2. Click "Add Server"
3. Command: `screen-vision-mcp`

### Environment Variable (Optional)

```bash
# Set mode (defaults to "personal")
export SCREEN_VISION_MODE=work      # Enable security controls
export SCREEN_VISION_MODE=personal  # Disable security (default)
```

Add to your shell profile (`~/.zshrc` or `~/.bashrc`) to persist.

## Two Modes: Work vs Personal

Screen Vision supports two modes to balance functionality with security.

### Personal Mode (Default)
- **No security scanning** — Full screen access, no PII/PCI detection
- **No rate limits** — Unlimited captures
- **No app deny-list** — All apps can be captured
- **No audit logs** — No tracking of captures

**Best for:** Personal projects, local development, screen demos

### Work Mode
- **Security scanning** — PII/PCI pattern detection in OCR text
- **Rate limits** — 200 captures/session, 2s interval, 5min max watch
- **App deny-list** — Blocks Slack, Teams, Zoom, banking apps
- **Call detection** — Blocks captures during active calls
- **Audit logs** — Tracks all captures with timestamps

**Best for:** Corporate environments, compliance requirements

Enable work mode:
```bash
export SCREEN_VISION_MODE=work
```

## Security Overview

When running in **work mode**, Screen Vision applies multiple layers of security:

### 1. PII/PCI Scanning
- Detects credit card numbers (Visa, MC, Amex, Discover)
- Detects SSNs, phone numbers, email addresses
- Scans OCR text in all capture tools
- **Actions:** BLOCK (rejects frame), REDACT (counts findings), ALLOW

### 2. App Deny-List
Blocks captures of:
- Communication apps: Slack, Microsoft Teams, Zoom, Google Meet
- Banking/finance: Chase, Wells Fargo, Bank of America
- Sensitive apps: 1Password, Bitwarden, Keychain Access

Customize by editing `src/screen_vision/security.py`.

### 3. Call Detection
Blocks captures when active audio calls are detected (Zoom, Teams, Meet, Slack).

### 4. Rate Limits
- **Max captures per session:** 200
- **Min interval between captures:** 2 seconds
- **Max watch duration:** 5 minutes
- **Max frames per watch:** 50

### 5. Audit Logs
All captures are logged with:
- Timestamp
- Tool used
- Active window
- Security scan results

Logs written to `~/.screen-vision/audit.log` (work mode only).

## Dependencies

### Core (always required)
- **Python 3.11+**
- `mcp[cli]>=1.0.0` — Model Context Protocol SDK
- `mss>=9.0.0` — Fast cross-platform screen capture
- `Pillow>=10.0.0` — Image processing
- `numpy>=1.24.0` — Array operations

### Full (optional, for OCR and audio)
Install with `pip install -e ".[full]"` or `pip install screen-vision[full]`

- `pytesseract>=0.3.10` — OCR wrapper (requires Tesseract: `brew install tesseract`)
- `faster-whisper>=1.0.0` — Audio transcription
- `sounddevice>=0.4.6` — Audio recording
- `opencv-python>=4.8.0` — Video processing (requires FFmpeg: `brew install ffmpeg`)

### Test
- `pytest>=8.0.0`
- `pytest-asyncio>=0.23.0`

Install with `pip install -e ".[test]"` or `pip install screen-vision[test]`

## Example Usage

### Basic Screen Capture
```python
# In Claude Code, just say:
"Take a screenshot of my screen"
"Capture the active window"
"What's on my screen?"
```

Claude will call `capture_screen()` or `capture_window()` and describe what it sees.

### Watch Screen with Audio
```python
# Watch for 60 seconds with audio transcription
"Watch my screen for 1 minute while I demo this feature"
```

Returns keyframes + transcript aligned to frames. Useful for:
- Recording demos/walkthroughs
- Analyzing user flows
- Debugging UI issues
- Capturing meeting notes with visual context

### Analyze Video File
```python
# Extract keyframes from a video
"Analyze the video at ~/Downloads/demo.mp4"
```

Returns 20 evenly-spaced keyframes. Useful for:
- Summarizing long videos
- Finding specific moments
- Creating thumbnails

### OCR Text Extraction
```python
# Read all text from screen
"Read the text on my screen"

# Read text from a region
"Read the text in the top-left corner (coordinates: 0,0,800,600)"
```

### Get Context Without Capturing
```python
# Lightweight query for window/cursor info
"What window am I in?"
"Where is my cursor?"
```

Uses `get_active_context()` — no image capture, just metadata.

## Development

### Running Tests
```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_capture.py -v
```

### Linting
```bash
# Install ruff
pip install ruff

# Lint
ruff check src/

# Format
ruff format src/
```

### Project Structure
```
screen-vision/
├── src/screen_vision/
│   ├── server.py          # MCP server with 8 tools
│   ├── config.py          # Work/personal mode config
│   ├── capture.py         # Screenshot capture (mss)
│   ├── context.py         # Window/cursor/monitor info
│   ├── ocr.py             # Tesseract OCR
│   ├── security.py        # PII/PCI scanner + deny-list
│   ├── watcher.py         # Screen watching + audio
│   └── video.py           # Video analysis
├── tests/                 # Pytest test suite
├── pyproject.toml         # Package config
└── README.md             # This file
```

## License

MIT License — see LICENSE file for details.

## Author

**Alex Vicuna** (alejandro.vicuna@agoda.com)
RTA (Rocket Travel by Agoda) AI Team

## Contributing

Issues and PRs welcome on GitLab:
https://gitlab.agodadev.io/partnertech/rta-ai/screen-vision
