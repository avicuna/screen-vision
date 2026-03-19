# Screen Vision MCP Server — Design Spec

## Overview

A Python MCP server that gives Claude Code the ability to see the user's screen on demand, understand what's being pointed at, and use video/audio as context in conversations. Runs on macOS. Two editions: Work (Agoda-managed, strict security) and Personal (relaxed).

**Goal:** When you say "look at my screen," Claude captures a screenshot, analyzes it with OCR, and responds with context-aware help. For longer workflows, Claude can watch your screen in real-time with audio, understanding what you're doing and saying.

**Architecture:** Single-process Python MCP server (monolith). All capture, processing, and security scanning happens in-process. No background daemons, no external services beyond Whisper for transcription.

**Platform:** macOS only (v1). Cross-platform deferred.

**Editions:** Config-driven via `SCREEN_VISION_MODE=work|personal`. Work mode enforces all corporate security controls. Personal mode relaxes them for non-corporate devices.

## MCP Tools (8 total)

### Screenshot Tools

#### `capture_screen(delay_seconds, monitor, scale)`

Capture the full screen with an optional delay for the user to switch windows.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `delay_seconds` | `int` | `3` | Seconds to wait before capture (user Alt+Tabs away from Claude) |
| `monitor` | `int` | `0` | Monitor index (0 = primary, use `list_monitors()` to discover) |
| `scale` | `float` | `0.5` | Scale factor (0.5 = 960x540 from 1080p) |

**Returns:** Base64-encoded JPEG image + metadata:
```json
{
  "image": "base64...",
  "format": "jpeg",
  "resolution": [960, 540],
  "cursor_position": [423, 312],
  "active_window": "Visual Studio Code — main.py",
  "timestamp": 1678901234.5,
  "ocr_text_near_cursor": "def process_booking(booking_id):",
  "security_redactions": 0
}
```

**Flow:**
1. Wait `delay_seconds` (user switches windows)
2. `mss` captures the screen
3. `Pillow` resizes by `scale` factor
4. `context.py` gets cursor position + active window title
5. `ocr.py` extracts text near cursor (200px radius at original resolution)
6. **[Work mode]** `security.py` scans OCR text for PII/PCI/secrets → redact or block
7. Encode as base64 JPEG (quality 75)
8. Return image + metadata

#### `capture_region(x, y, width, height, scale)`

Capture a specific screen region. Full resolution by default since regions are already small.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `x` | `int` | required | Left edge |
| `y` | `int` | required | Top edge |
| `width` | `int` | required | Width in pixels |
| `height` | `int` | required | Height in pixels |
| `scale` | `float` | `1.0` | Scale factor (1.0 = native resolution) |

**Returns:** Same format as `capture_screen()`.

#### `capture_window(window_title, scale)`

Capture a specific application window by title substring. Avoids the occlusion problem (Claude Code covering content).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `window_title` | `str` | required | Substring match against window titles |
| `scale` | `float` | `0.5` | Scale factor |

**Implementation:** Uses `osascript` to enumerate windows and macOS `screencapture -l <windowid>` to capture a specific window without occlusion.

**[Work mode]** Checks `window_title` against the app deny-list before capturing. Blocked apps: Slack, Microsoft Teams, Mail, Outlook, 1Password, LastPass, Bitwarden, Keychain Access.

**Returns:** Same format as `capture_screen()`, with `active_window` set to the captured window's full title.

#### `list_monitors()`

Returns available monitors with dimensions and positions. No parameters.

**Returns:**
```json
{
  "monitors": [
    {"index": 0, "width": 1920, "height": 1080, "x": 0, "y": 0, "is_primary": true},
    {"index": 1, "width": 2560, "height": 1440, "x": 1920, "y": 0, "is_primary": false}
  ]
}
```

### Live Watching Tools

#### `watch_screen(duration_seconds, interval_seconds, include_audio, max_frames)`

Watch the screen for a period of time, capturing keyframes at smart intervals with optional mic audio transcription.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `duration_seconds` | `int` | `60` | How long to watch (max 300 for work, unlimited personal) |
| `interval_seconds` | `float` | `4.0` | Base interval between frame captures |
| `include_audio` | `bool` | `True` | Record mic audio and transcribe with Whisper |
| `max_frames` | `int` | `30` | Maximum frames to return. Hard cap: 50 in work mode, uncapped in personal. |

**Flow:**
1. Start two parallel threads:
   - **Frame sampler thread:** Every `interval_seconds`, capture a frame. Compare to previous frame using perceptual hash — skip if < 2% difference (scene hasn't changed). Store keyframes in memory list.
   - **Audio recorder thread:** Record mic via `sounddevice` into an in-memory buffer. **[Work mode]** Check if Zoom/Teams/Slack/Meet is using the mic → if yes, block audio recording with a warning.
2. Wait for `duration_seconds` or until `max_frames` reached.
3. Process results:
   - **[Work mode]** Security-scan each frame (OCR → PII/PCI scan → redact/block)
   - Transcribe audio buffer with Whisper (word-level timestamps)
   - Sync transcript segments to nearest keyframes (±2 seconds)
4. Return structured result.

**Returns:**
```json
{
  "keyframes": [
    {
      "index": 0,
      "image": "base64...",
      "timestamp": 1678901234.5,
      "active_window": "Chrome — Grafana Dashboard",
      "scene_changed": true,
      "ocr_summary": "CPU usage at 87%, Memory: 12.4GB/16GB"
    }
  ],
  "transcript": [
    {
      "text": "So the CPU spike started around 2pm...",
      "start_time": 12.3,
      "end_time": 15.1,
      "nearest_frame_index": 3
    }
  ],
  "duration_actual": 58.2,
  "frames_captured": 14,
  "frames_skipped_duplicate": 6,
  "audio_recorded": true,
  "security_redactions": 2
}
```

#### `analyze_video(file_path, start_time, end_time, max_frames)`

Extract key frames from a local video file using ffmpeg. Smart sampling via scene change detection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | required | Path to local video file |
| `start_time` | `float` | `0` | Start time in seconds |
| `end_time` | `float \| None` | `None` | End time (None = end of video) |
| `max_frames` | `int` | `20` | Maximum keyframes to extract |

**Flow:**
1. Validate file exists and is a video format
2. Use `ffmpeg` to extract frames at scene changes (`-vf "select=gt(scene,0.3)"`)
3. If fewer than `max_frames` from scene detection, supplement with periodic samples
4. Optionally extract and transcribe audio track with Whisper
5. **[Work mode]** Security-scan each frame
6. Return keyframes + transcript

**Returns:** Same format as `watch_screen()`.

### Context Tools

#### `read_screen_text(region)`

OCR the screen or a specific region. Returns extracted text with approximate positions.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `region` | `tuple \| None` | `None` | (x, y, width, height) or None for full screen |

**Returns:**
```json
{
  "text": "Full extracted text...",
  "blocks": [
    {"text": "def process_booking(", "bbox": [120, 340, 380, 360], "confidence": 0.95}
  ],
  "active_window": "VS Code — main.py",
  "security_redactions": 0
}
```

**[Work mode]** Scans extracted text for PII/PCI before returning. Redacts sensitive matches.

#### `get_active_context()`

Lightweight metadata grab. No screenshot, no OCR — just context. Cheap to call.

**Returns:**
```json
{
  "active_window": "Visual Studio Code — main.py",
  "cursor_position": [423, 312],
  "monitors": [{"width": 1920, "height": 1080}],
  "timestamp": 1678901234.5
}
```

## Security Architecture

### Threat Model

Screen capture on a corporate device is inherently high-risk. The tool can see:
- Customer PII (names, emails, phone numbers, passport numbers, addresses) in Agoda's admin tools
- PCI data (credit card numbers) in payment interfaces
- Credentials (API keys, passwords, bearer tokens) in terminals and browser developer tools
- Confidential communications (Slack DMs, emails, HR documents)
- Meeting content from video calls (recording third parties without consent)

All captured data is sent to an LLM for analysis. Without controls, this creates a data exfiltration pipeline that bypasses all existing DLP controls.

### Security Principle: Defense in Depth

Every frame passes through multiple security layers before leaving the machine:

```
Screen Capture
    ↓
Layer 1: App Deny-List (is this app blocked?)
    ↓
Layer 2: Local OCR (extract all visible text)
    ↓
Layer 3: PCI Scanner (credit card numbers, Luhn-validated)
    ↓  → BLOCK if PCI found (never send, never redact-and-send)
Layer 4: PII Scanner (emails, phones, passports, IPs)
    ↓  → REDACT (black box the region in the image, replace text)
Layer 5: Secrets Scanner (API keys, tokens, passwords)
    ↓  → BLOCK if secrets found
Layer 6: Audit Log (record what was captured, when, what was redacted/blocked)
    ↓
Layer 7: Encode + Send to Claude via GenAI Gateway
```

### PII/PCI/Secrets Scanner (`security.py`)

#### PCI Patterns (always BLOCK — PCI-DSS requires zero tolerance)
```python
PCI_PATTERNS = [
    r'\b4[0-9]{12}(?:[0-9]{3})?\b',       # Visa
    r'\b5[1-5][0-9]{14}\b',                 # Mastercard
    r'\b3[47][0-9]{13}\b',                  # Amex
    r'\b6(?:011|5[0-9]{2})[0-9]{12}\b',    # Discover
]
# All matches validated with Luhn algorithm to avoid false positives
```

**OCR False-Negative Mitigation (PCI):** OCR may misread digits in card numbers. To address this:
- Block any frame where OCR detects a Luhn-candidate sequence with **any** character confidence below 0.8
- If OCR confidence is low overall (< 70% average), treat the frame as potentially containing undetectable sensitive data and block it with a warning: `"Frame blocked: OCR confidence too low to guarantee PCI safety"`
- This is documented as a known limitation — OCR-based PCI detection is best-effort, not a certified PCI scanning solution. The primary PCI control is "don't open payment pages while using Screen Vision."

#### PII Patterns (REDACT — black box in image, mask in text)
```python
PII_PATTERNS = [
    # Email addresses
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    # Phone numbers — require leading indicator to reduce false positives
    r'(?:tel:|phone:|call:|mobile:|☎|\+)\s*[0-9][\d\s\-().]{7,15}',
    # IP addresses — exclude loopback, link-local, and version-like strings
    # Only flag IPs in non-trivial ranges (10.x, 172.16-31.x, 192.168.x, or public)
    r'\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b',
]
# Note: Passport numbers excluded from v1 — the regex `[A-Z]{1,2}[0-9]{6,9}`
# produces excessive false positives on version numbers, git SHAs, error codes.
# Will revisit with country-specific patterns in v2.
```

**Known limitation (multi-language):** PII patterns are Latin-script only. Screens may contain Thai, CJK, or other non-Latin PII that bypasses detection. Documented as a v1 limitation. Mitigation: the security scanner logs a warning when significant non-Latin text is detected, suggesting manual review.

#### Secrets Patterns (always BLOCK)
```python
SECRET_PATTERNS = [
    # Generic credential assignments
    r'(?i)(password|passwd|pwd)\s*[:=]\s*\S+',
    r'(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*\S+',
    # GitHub tokens
    r'(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}',
    # GitLab personal access tokens (Agoda uses GitLab daily)
    r'glpat-[A-Za-z0-9_\-]{20,}',
    # AWS access keys
    r'AKIA[0-9A-Z]{16}',
    # Vault tokens (HashiCorp Vault — used at Agoda)
    r'(?:hvs\.|s\.)[A-Za-z0-9]{20,}',
    # Bearer / Authorization headers
    r'(?i)bearer\s+[a-zA-Z0-9\-._~+/]+=*',
    # JWT tokens
    r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
    # Slack tokens
    r'xox[bpsa]-[A-Za-z0-9\-]{10,}',
    # SSH private key headers
    r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
    # GCP service account key indicator
    r'"private_key"\s*:\s*"-----BEGIN',
    # Generic Authorization headers
    r'(?i)authorization\s*:\s*\S+',
    # Database connection strings with passwords
    r'(?i)(?:mysql|postgres|mongodb|redis)://[^:]+:[^@]+@',
]
# Pattern list is configurable via SCREEN_VISION_EXTRA_SECRET_PATTERNS env var
# (newline-separated regexes) so it can be updated without redeploying.
```

#### Image Redaction

When PII is detected via OCR, the scanner:
1. Gets bounding boxes from `pytesseract.image_to_data()`
2. Draws solid black rectangles over the sensitive regions
3. Adds a red `[REDACTED]` label so Claude knows something was removed
4. Returns the redacted image + a count of redactions

#### Scan Result Actions

| Finding Type | Action | Rationale |
|-------------|--------|-----------|
| PCI (credit card) | **BLOCK entire frame** | PCI-DSS: no card data in any system not in scope |
| Secrets (API key, token) | **BLOCK entire frame** | Credential exposure = immediate incident |
| PII (email, phone, passport) | **REDACT region** | Mask the data, keep the rest of the frame useful |
| App deny-list match | **SKIP frame** | Don't even capture — preemptive |

### App Deny-List (Work Mode)

Applications that are **never captured**, even if the user explicitly requests:

```python
BLOCKED_APPS = [
    'Slack',                # DMs, private channels, confidential threads
    'Microsoft Teams',      # Same
    'Mail', 'Outlook',      # Email content
    '1Password', 'LastPass', 'Bitwarden',  # Password managers
    'Keychain Access',      # macOS keychain
    'Messages',             # iMessage
]
```

**Visibility check (not just foreground):** The deny-list checks ALL visible windows on screen, not just the active one. For `capture_screen()` and `capture_region()`, enumerate all on-screen windows via `CGWindowListCopyWindowInfo` and check if any blocked-app window overlaps the captured region. If a blocked app is visible anywhere in the capture area:
- Option 1: BLOCK the entire capture with an error
- Option 2: REDACT the blocked app's window region (black box) and capture the rest

Default: BLOCK (safer). Configurable to REDACT via `SCREEN_VISION_DENYLIST_ACTION=redact`.

When a blocked app is detected:
- Return an error: `"Cannot capture: [Slack] is visible in the capture area. Minimize it or use capture_window() to target a specific app."`
- Log the blocked attempt to the audit log

### Rate Limiting (Work Mode)

Prevent abuse from runaway tool loops or prompt injection:

| Control | Limit | Rationale |
|---------|-------|-----------|
| Screenshot rate | Max 1 per 2 seconds | Prevents continuous surveillance |
| `watch_screen` concurrency | Max 1 session at a time | Prevents parallel watchers |
| Session capture budget | Max 200 captures per Claude session | Requires explicit user re-auth to extend |
| `analyze_video` file size | Max 500MB | Prevents memory exhaustion |
| `analyze_video` duration | Max 600 seconds | Bounds processing time |

When a rate limit is hit, return: `"Rate limit: max 1 screenshot per 2 seconds. Try again shortly."`

The session capture budget resets when Claude Code restarts. To extend mid-session, the user must explicitly approve via a confirmation prompt.

### Audio Security Controls

#### Call Detection (Work Mode — Mandatory)

Before activating the microphone, check if a video/audio call app is using it:

```python
CALL_APPS = ['zoom.us', 'Microsoft Teams', 'Slack', 'FaceTime', 'Google Chrome']  # Chrome = Google Meet

def is_call_active() -> bool:
    """Check if any call app is currently using the microphone."""
    # macOS: Use coreaudiod / IOKit to check which processes hold the mic
    # Fallback: Check if known call apps are running + mic permission active
```

If a call is active:
- **BLOCK** audio recording entirely
- Return a warning: `"Audio recording blocked: [Zoom] is using the microphone. Recording calls requires third-party consent."`
- Still allow screenshot capture (visual context without audio)

#### Legal Compliance Context

Recording audio on corporate devices has legal implications:
- **Thailand (PDPA):** Consent required for recording personal data
- **EU (GDPR):** Explicit consent + legitimate interest basis required
- **California:** Two-party consent state — all parties must consent
- **Singapore (PDPA):** Consent + purpose limitation

The call detection control prevents inadvertent recording of third parties who haven't consented. This is the primary legal risk and must be enforced.

### Audit Logging (Work Mode)

Every capture action is logged locally:

```json
{
  "timestamp": "2026-03-19T14:23:45Z",
  "action": "capture_screen",
  "active_window": "Chrome — Agoda Admin",
  "security_scan": {
    "pci_found": false,
    "pii_found": 2,
    "secrets_found": 0,
    "redactions_applied": 2,
    "blocked": false
  },
  "result": "sent_with_redactions"
}
```

Logs stored at `~/.screen-vision/audit.log`. Log rotation: 7 days, max 50MB.

These logs exist so that if security asks "what data has this tool captured and sent?", there's a complete audit trail. The logs contain metadata only — never the actual image data.

### Data Handling

| Aspect | Work Mode | Personal Mode |
|--------|-----------|---------------|
| Disk persistence | **Never** — all frames/audio in-memory only | Optional save |
| Data routing | GenAI Gateway only (Agoda infra) | Direct API calls OK |
| Frame retention | Gone when tool returns | Gone when tool returns |
| Audio retention | Gone after Whisper transcription | Gone after transcription |
| Audit logs | Always (metadata only, 7-day retention) | None |
| Temp files | Never created | Never created |

### macOS Permissions

The tool requires two macOS permissions:
1. **Screen Recording** — System Preferences → Privacy & Security → Screen Recording
2. **Microphone** — System Preferences → Privacy & Security → Microphone

On Agoda-managed devices, these may need MDM (Mobile Device Management) pre-approval. The tool should detect missing permissions and provide clear instructions:

```
Screen Recording permission not granted.
Go to: System Preferences → Privacy & Security → Screen Recording
Add: Terminal (or your IDE) to the allowed list.
```

## Work vs Personal Edition

The two editions share 100% of the code. The only difference is a config flag:

```
SCREEN_VISION_MODE=work    # Env var or config file
SCREEN_VISION_MODE=personal
```

### What `work` mode enables:
- PII/PCI/secrets scanning on every frame (mandatory, cannot be disabled)
- App deny-list enforcement
- Audio call detection and blocking
- Audit logging
- GenAI Gateway routing (direct API calls blocked)
- Max watch duration: 300 seconds
- Visual capture indicator in menu bar

### What `personal` mode changes:
- PII/PCI scanning: optional (off by default, enable with `--scan`)
- No app deny-list
- Call detection: warning only (not blocked)
- No audit logging
- Direct API calls allowed
- No watch duration limit
- Visual capture indicator still on (good practice)

### Two Separate Repositories

Following the AI Council pattern:
- **Work (GitLab):** `gitlab.agodadev.io/partnertech/rta-ai/screen-vision` — Agoda GenAI Gateway, security controls, team-shared
- **Personal (GitHub):** `github.com/avicuna/screen-vision` — Direct API keys, relaxed security, experimentation

## Internal Architecture

### File Structure

```
screen-vision/
├── pyproject.toml
├── README.md
├── .mcp.json                    # MCP server config for testing
└── src/screen_vision/
    ├── __init__.py
    ├── server.py                # FastMCP server — 8 tool definitions
    ├── capture.py               # mss screenshot + region + window capture
    ├── watcher.py               # Live screen watching + adaptive frame sampling
    ├── audio.py                 # Mic recording (sounddevice) + Whisper transcription
    ├── video.py                 # Video file analysis via ffmpeg
    ├── security.py              # PII/PCI scanner, app deny-list, call detection, audit log
    ├── ocr.py                   # pytesseract OCR wrapper
    ├── context.py               # Active window, cursor position, monitor info (osascript)
    └── config.py                # Mode (work/personal), settings, thresholds
```

### Dependencies

**Core (required):**
- `mcp[cli]` — MCP server framework (FastMCP)
- `mss` — Cross-platform screen capture
- `Pillow` — Image processing, resizing, JPEG encoding
- `sounddevice` — Microphone recording
- `numpy` — Frame comparison (pixel diff)

**Enhanced features (optional, installed via extras):**
- `pytesseract` — OCR (requires `tesseract` binary: `brew install tesseract`)
- `faster-whisper` — Fast Whisper transcription (or `openai-whisper`)
- `opencv-python` — Perceptual hashing for scene change detection
- `ffmpeg-python` — Video file frame extraction (requires `ffmpeg` binary: `brew install ffmpeg`)

**Install:**
```bash
pip install -e .                    # Core only
pip install -e ".[full]"           # All features
brew install tesseract ffmpeg       # System dependencies
```

### Frame Processing Pipeline

Every frame goes through the same pipeline regardless of source (screenshot, watch, video):

```python
def process_frame(image: Image.Image, config: Config) -> FrameResult:
    """Standard pipeline for every captured frame."""
    # 1. Resize
    if config.scale != 1.0:
        image = image.resize(scaled_size, Image.LANCZOS)

    # 2. Get context
    cursor_pos = get_cursor_position()
    active_window = get_active_window()

    # 3. OCR (always, for both context and security)
    ocr_result = run_ocr(image)
    ocr_near_cursor = extract_text_near(ocr_result, cursor_pos, radius=200)

    # 4. Security scan [work mode]
    if config.is_work_mode:
        scan = security_scan(ocr_result.text, image, ocr_result.boxes)
        if scan.should_block:
            return FrameResult(blocked=True, reason=scan.block_reason)
        if scan.redactions:
            image = apply_redactions(image, scan.redactions)

    # 5. Encode
    base64_image = encode_jpeg(image, quality=75)

    return FrameResult(
        image=base64_image,
        cursor_position=cursor_pos,
        active_window=active_window,
        ocr_near_cursor=ocr_near_cursor,
        security_redactions=len(scan.redactions) if config.is_work_mode else 0,
    )
```

### Scene Change Detection

For `watch_screen()`, we need to detect when the screen actually changes to avoid sending duplicate frames:

```python
def has_scene_changed(prev_frame: bytes, curr_frame: bytes, threshold: float = 0.02) -> bool:
    """Compare frames using normalized pixel difference."""
    prev = np.array(Image.open(io.BytesIO(prev_frame)).resize((160, 90)))
    curr = np.array(Image.open(io.BytesIO(curr_frame)).resize((160, 90)))
    diff = np.abs(prev.astype(np.int16) - curr.astype(np.int16)).mean() / 255.0
    return diff > threshold
```

Tiny comparison images (160x90) for speed. Only send a frame if the scene changed by more than 2%. This typically reduces 15 frames/minute to 3-5 meaningful frames.

### Audio-Video Synchronization

Whisper provides word-level timestamps. We sync transcript segments to the nearest keyframe:

```python
def sync_transcript_to_frames(transcript: list, keyframes: list) -> list:
    """Attach each transcript segment to its nearest keyframe."""
    for segment in transcript:
        segment_time = (segment.start + segment.end) / 2
        nearest = min(keyframes, key=lambda f: abs(f.timestamp - segment_time))
        segment.nearest_frame_index = nearest.index
    return transcript
```

## Testing Strategy

```
tests/
├── test_capture.py        # Screenshot capture, region, window, scaling
├── test_watcher.py        # Frame sampling, scene detection, duration limits
├── test_audio.py          # Mic recording, call detection, Whisper integration
├── test_video.py          # Video file analysis, ffmpeg extraction
├── test_security.py       # PII/PCI scanning, redaction, app deny-list, audit log
├── test_ocr.py            # OCR extraction, text-near-cursor
├── test_context.py        # Window title, cursor position, monitors
├── test_config.py         # Work vs personal mode, settings
└── test_server.py         # MCP tool integration tests
```

Mock `mss` captures with pre-built test images. Mock `sounddevice` with pre-recorded audio buffers. Test security scanner with images containing known PCI/PII patterns.

## Dependency Requirements by Mode

| Dependency | Work Mode | Personal Mode |
|-----------|-----------|---------------|
| `mss` | Required | Required |
| `Pillow` | Required | Required |
| `sounddevice` | Required (for audio tools) | Required (for audio tools) |
| `pytesseract` + `tesseract` binary | **Required** (security scanning depends on OCR) | Optional |
| `faster-whisper` | Required (for audio tools) | Optional |
| `opencv-python` | Optional (falls back to pixel diff) | Optional |
| `ffmpeg` binary | Required (for `analyze_video`) | Optional |

**Work mode startup check:** On server init, verify all required dependencies are installed. If `tesseract` is missing in work mode, **refuse to start** and print: `"ERROR: tesseract is required in work mode for PII/PCI security scanning. Install: brew install tesseract"`

**Graceful degradation (personal mode):** If optional deps are missing, individual tools return a clear error: `"analyze_video requires ffmpeg. Install: brew install ffmpeg"`. Other tools continue working.

## Error Response Schema

All tools use a standard error format:

```json
{
  "error": true,
  "code": "SECURITY_BLOCKED",
  "message": "Frame blocked: PCI data detected (Visa card ending ****1234)",
  "details": {
    "finding_type": "PCI",
    "action": "BLOCK",
    "blocked_patterns": 1
  }
}
```

Error codes:
- `SECURITY_BLOCKED` — PCI/secrets detected, frame not sent
- `SECURITY_REDACTED` — PII found and redacted (frame still sent, info only)
- `APP_BLOCKED` — Blocked app visible in capture area
- `CALL_ACTIVE` — Audio blocked because a call is in progress
- `RATE_LIMITED` — Too many captures in quick succession
- `PERMISSION_DENIED` — macOS screen recording or mic permission not granted
- `DEPENDENCY_MISSING` — Required dependency not installed
- `FILE_TOO_LARGE` — Video file exceeds size limit
- `TIMEOUT` — Processing exceeded time limit

## Image Processing Notes

**EXIF stripping:** All images have EXIF metadata stripped before encoding. EXIF can contain GPS coordinates, device serial numbers, and timestamps. Stripping is done via Pillow's `image.save()` without `exif` parameter (Pillow strips by default when not explicitly passed).

**JPEG quality:** Default 75 for screenshots, 65 for watch_screen frames (higher volume, lower per-frame importance).

## Concurrency Model

- **One `watch_screen` session at a time.** If a second `watch_screen` is called while one is running, return an error: `"A watch session is already active. Call stop or wait for it to complete."`
- **`capture_screen` during `watch_screen`:** Allowed — the screenshot is independent of the watcher. Both use `mss` which is thread-safe.
- **`read_screen_text` during `watch_screen`:** Allowed — OCR runs independently.
- **Thread safety:** The frame sampler and audio recorder threads communicate via `threading.Event` for stop signals and `queue.Queue` for frame data. No shared mutable state.

## Non-Goals

- No continuous/ambient capture — always user-triggered
- No remote screen viewing — localhost only
- No screen control (mouse/keyboard) — read-only vision, not Computer Use
- No cloud storage of captures — everything in-memory, ephemeral
- No Windows/Linux support in v1 — macOS only
- No system audio capture in v1 — microphone only
- No real-time streaming to Claude — batch frames per tool call

## Repositories

- **Work (GitLab):** `gitlab.agodadev.io/partnertech/rta-ai/screen-vision`
- **Personal (GitHub):** `github.com/avicuna/screen-vision`
- **Marketplace:** Plugin will be added to `rta-ai-marketplace` after v1 ships
