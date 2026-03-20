# Phone Camera Integration — Design Spec

## Overview

Extend the Screen Vision MCP Server so Claude can see the real world through your iPhone camera. Two approaches based on edition:

- **Work edition:** AirDrop/file-drop a photo → `analyze_image(path)` tool reads it. No network listener. No corporate security concerns.
- **Personal edition:** Full live camera stream via WebSocket. Phone opens a web page served by the MCP server, streams JPEG frames + mic audio. QR-code pairing for authentication.

## Work Edition: File-Drop Approach

### New MCP Tool

#### `analyze_image(file_path, prompt)`

Analyze a dropped image file (from AirDrop, screenshot, saved photo).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | required | Path to image file (JPEG, PNG, HEIC) |
| `prompt` | `str` | `""` | Optional context: "this is a whiteboard" or "look at this circuit" |

**Flow:**
1. Validate file exists, is an image format, reasonable size (<50MB)
2. Load with Pillow, resize if larger than 2048px on longest side
3. Strip EXIF metadata
4. **[Work mode]** Security scan via OCR → PII/PCI/secrets check
5. Encode as base64 JPEG
6. Return image + metadata (dimensions, file name, any OCR text)

**Returns:** Same format as `capture_screen()` but with `source: "file"` instead of `source: "screen"`.

This is simple, safe, and requires no network changes. AirDrop to Mac → file appears in Downloads → Claude reads it.

## Personal Edition: Live Camera Stream

### Architecture

```
iPhone (Safari)                    Desktop (MCP Server)
┌──────────────┐    WiFi LAN    ┌───────────────────────┐
│  Camera      │  WebSocket     │  Camera Bridge        │
│  getUserMedia│ ──────────►    │  (HTTPS + WSS)        │
│              │ wss://IP:8443  │                       │
│  Mic         │                │  Frame Queue          │
│  getUserMedia│ ──────────►    │  (in-memory ring buf) │
│              │  PCM audio     │                       │
│  Web Page    │                │  Audio Buffer         │
│  (served by  │                │  (in-memory)          │
│   MCP server)│                │                       │
└──────────────┘                └───────────┬───────────┘
                                            │ internal
                                ┌───────────▼───────────┐
                                │  MCP Server (stdio)   │
                                │  capture_camera()     │
                                │  watch_camera()       │
                                │  phone_status()       │
                                │  show_pairing_qr()    │
                                └───────────┬───────────┘
                                            │ stdio
                                ┌───────────▼───────────┐
                                │  Claude Code          │
                                └───────────────────────┘
```

### Authentication: QR-Code Pairing

Physical proximity is the trust anchor. Only you can see your screen.

**Pairing flow:**
1. User asks Claude to connect phone, or calls `show_pairing_qr()`
2. MCP server generates:
   - A one-time pairing token (128-bit random, hex-encoded)
   - A URL: `https://<desktop-lan-ip>:8443?token=<pairing-token>`
   - A QR code encoding that URL
3. QR code displayed in terminal (ASCII art via `qrcode` library)
4. User scans QR with iPhone camera → Safari opens the URL
5. Web page connects WebSocket, sends token in first message
6. Server validates token (one-use, expires after 60 seconds)
7. Session established → token invalidated → camera streaming begins

**Security properties:**
- Token is one-time use (cannot be replayed)
- Token expires in 60 seconds (window of vulnerability is small)
- Only someone who can see the terminal can get the token
- All traffic encrypted via TLS 1.3

### TLS Setup

iPhone Safari requires HTTPS for `getUserMedia()`. We need local TLS:

**Setup (one-time):**
```bash
# Install mkcert
brew install mkcert
mkcert -install  # Installs local CA in system trust store

# Generate cert for local IP
mkcert 192.168.1.100  # Your desktop's LAN IP
# Creates: 192.168.1.100.pem + 192.168.1.100-key.pem
```

**Phone trust (one-time):**
1. AirDrop the CA certificate (`~/Library/Application Support/mkcert/rootCA.pem`) to iPhone
2. Settings → General → VPN & Device Management → Install profile
3. Settings → General → About → Certificate Trust Settings → Enable for mkcert

This is a 2-3 minute one-time setup. Documented with screenshots in README.

### Phone Web App

Served by the MCP server at `https://<ip>:8443/`. Single HTML file with:

```html
<!-- Key features -->
<video id="camera" autoplay playsinline></video>
<canvas id="canvas" style="display:none"></canvas>
<button id="startBtn">▶ Start Streaming</button>
<button id="micBtn">🎤 Mic</button>
<div id="status">Connecting...</div>
```

**Camera streaming:**
- `getUserMedia({ video: { facingMode: "environment" } })` — rear camera
- Canvas captures frame every 250ms (4 FPS default)
- JPEG quality 70, ~30-50KB per frame
- Sent as binary WebSocket message with type byte prefix: `0x01` = frame

**Audio streaming (optional):**
- `getUserMedia({ audio: true })` — phone mic
- ScriptProcessor captures PCM 16-bit at 16kHz
- Sent as binary WebSocket message: `0x02` = audio chunk
- Transcribed on desktop via Whisper

**UI features:**
- Front/rear camera toggle
- Start/pause streaming
- Mic on/off
- Connection status indicator
- Frame rate display

### Camera Bridge (Desktop-Side)

A background thread in the MCP server that:
- Runs HTTPS server on port 8443 (configurable)
- Serves the phone web app at `/`
- Accepts WebSocket connections at `/ws`
- Validates pairing token on first message
- Queues incoming frames in an in-memory ring buffer (last 30 frames)
- Queues incoming audio in an in-memory buffer
- Only starts when explicitly enabled (not always listening)
- Auto-shuts down after 10 minutes of no authenticated connection

### New MCP Tools (Personal Edition)

#### `show_pairing_qr()`

Generate and display a QR code for phone pairing.

**Returns:**
```json
{
  "qr_ascii": "█▀▀▀▀▀█...",
  "url": "https://192.168.1.100:8443?token=abc123...",
  "expires_in_seconds": 60,
  "instructions": "Scan this QR code with your iPhone camera. Safari will open the camera page."
}
```

#### `capture_camera()`

Grab the latest frame from the connected phone camera.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | `str` | `""` | Context for what you're pointing at |

**Returns:** Base64 image + metadata (timestamp, frame age, phone connected status).

If no phone is connected, returns error: `"No phone connected. Use show_pairing_qr() to connect."`

#### `watch_camera(duration_seconds, include_audio, max_frames)`

Stream phone camera frames for a duration, like `watch_screen()` but from the phone.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `duration_seconds` | `int` | `30` | How long to watch |
| `include_audio` | `bool` | `True` | Include phone mic audio |
| `max_frames` | `int` | `20` | Maximum frames to return |

Uses scene change detection on incoming frames (skip duplicates). Transcribes phone audio via Whisper on desktop.

**Returns:** Same format as `watch_screen()` — keyframes + transcript.

#### `phone_status()`

Check phone connection status.

**Returns:**
```json
{
  "connected": true,
  "phone_ip": "192.168.1.105",
  "last_frame_age_ms": 250,
  "frames_received": 142,
  "session_duration_seconds": 45.2,
  "audio_streaming": true
}
```

### Server Lifecycle

- Camera bridge **only starts** when `show_pairing_qr()` is called or camera mode is explicitly enabled
- **Auto-shutdown** after 10 minutes of no authenticated connection
- **On MCP server exit:** bridge shuts down, all buffers cleared
- **Frame/audio buffers:** In-memory ring buffers only. No disk persistence.

### Security Summary (Personal Edition)

| Control | Implementation |
|---------|---------------|
| Authentication | QR-code pairing with one-time token (128-bit, 60s expiry) |
| Encryption | TLS 1.3 via mkcert (local CA) |
| Network binding | LAN IP only (not 0.0.0.0, not localhost) |
| Session management | One session at a time, auto-disconnect idle |
| Data persistence | Zero — all frames/audio in memory only |
| Server lifecycle | On-demand only, auto-shutdown after 10 min idle |
| Input validation | Max frame size 1MB, max 10 FPS, reject malformed data |
| Logging | Never log tokens, frames, or audio. Log events only. |

## Dependencies (New)

- `qrcode` — QR code generation for terminal display
- `aiohttp` or `websockets` — HTTPS + WebSocket server
- `mkcert` — Local TLS certificate generation (system tool, not pip)

## File Changes

### New Files
- `src/screen_vision/camera_bridge.py` — HTTPS + WebSocket server, frame queue, pairing
- `src/screen_vision/phone_app.py` — Phone web app HTML (served to browser)
- `tests/test_camera_bridge.py` — Bridge tests
- `tests/test_analyze_image.py` — File-drop analyze tests

### Modified Files
- `src/screen_vision/server.py` — Add 5 new tools: analyze_image, capture_camera, watch_camera, phone_status, show_pairing_qr
- `src/screen_vision/config.py` — Add camera bridge settings (port, auto-shutdown timeout)
- `pyproject.toml` — Add qrcode, websockets dependencies

## Non-Goals

- No native iPhone app — web app only (no App Store review needed)
- No persistent camera recording — frames are ephemeral
- No remote access outside LAN — same WiFi only
- No multi-phone support — one phone at a time
- No work edition WebSocket (AirDrop file-drop only for corporate)
