# Screen Vision MCP Server

> Give Claude Code the ability to see your screen

Screen Vision lets Claude capture screenshots, watch your screen in real-time with audio transcription, analyze video files, and read text via OCR. It runs locally as an MCP server — Claude sees what you see, when you ask.

## Quick Start

```bash
pip install screen-vision
```

Then add to your Claude Code MCP config (`.mcp.json`):
```json
{
  "mcpServers": {
    "screen-vision": {
      "command": "python3",
      "args": ["-m", "screen_vision"]
    }
  }
}
```

**Optional system deps** (not required — tools gracefully degrade without them):
```bash
brew install tesseract   # Enables OCR (read_screen_text)
brew install ffmpeg      # Enables video analysis (analyze_video)
```

## What You Can Say

```
"Take a screenshot of my screen"          → capture_screen
"Capture the Chrome window"               → capture_window
"Watch my screen for 1 minute"            → watch_screen (with audio transcription)
"Analyze the video at ~/Downloads/demo.mp4" → analyze_video
"Read the text on my screen"              → read_screen_text
"What window am I in?"                    → get_active_context (no screenshot)
"What's on my screen right now?"          → understand_screen (AI analysis)
"Analyze this photo I AirDropped"         → analyze_image
```

## Tools (14)

| Tool | What it does | Needs |
|------|-------------|-------|
| `capture_screen` | Full screen capture with delay + multi-monitor | — |
| `capture_region` | Capture a specific rectangular area | — |
| `capture_window` | Capture a window by title | — |
| `list_monitors` | List displays with resolutions | — |
| `get_active_context` | Window/cursor/monitor info (no image) | — |
| `read_screen_text` | OCR text extraction from screen | tesseract |
| `understand_screen` | AI-powered screen analysis | Anthropic API key |
| `analyze_image` | Analyze a dropped/AirDropped image file | — |
| `watch_screen` | Watch screen with frame sampling + audio | ffmpeg (audio) |
| `analyze_video` | Extract keyframes from video files | ffmpeg |
| `capture_camera` | Grab latest frame from phone camera | — |
| `watch_camera` | Stream phone camera with scene detection + audio | — |
| `show_pairing_qr` | Show QR code to connect phone camera | — |
| `phone_status` | Check phone camera connection status | — |

## Security

Screen Vision includes security controls for corporate environments:

- **PII/PCI scanning** — Detects credit card numbers, SSNs, phone numbers, email addresses in OCR text
- **App deny-list** — Blocks captures of Slack, Teams, Zoom, banking apps, password managers
- **Call detection** — Blocks captures during active audio calls
- **Rate limits** — 200 captures/session, 2s minimum interval, 5min max watch duration
- **Audit logs** — All captures logged to `~/.screen-vision/audit.log`

Set `SCREEN_VISION_MODE=work` to enable all security controls. Default mode is `personal` (no restrictions).

## Dependencies

**Core** (always installed): `mcp[cli]`, `mss`, `Pillow`, `numpy`, `httpx`

**Optional** (`pip install screen-vision[full]`):
- `pytesseract` — OCR (needs `brew install tesseract`)
- `faster-whisper` — Audio transcription
- `sounddevice` — Audio recording
- `opencv-python` — Video processing
- `paddleocr` — Alternative OCR engine

**Python 3.11+ required.**

## Development

```bash
pip install -e ".[full,test]"
pytest tests/ -v
ruff check src/
```

## Author

**Alex Vicuna** — [github.com/avicuna](https://github.com/avicuna)

## Contributing

Issues and PRs welcome: https://github.com/avicuna/screen-vision
