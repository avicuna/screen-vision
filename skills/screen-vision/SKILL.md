---
name: screen-vision
description: Give Claude the ability to see your screen, watch it in real-time with audio transcription, analyze video files, and extract text via OCR. 10 tools for screen capture, video analysis, image analysis, and OCR. Use when the user wants to show something on their screen, record a demo, analyze a video, or extract text.
---

# Screen Vision — See Your Screen from Claude Code

Use this skill when the user wants Claude to see what's on their screen, watch their screen over time, analyze videos, or extract text.

## When to Use

- User says "look at my screen", "what's on my screen", "take a screenshot"
- User says "watch my screen", "record this demo", "capture what I'm doing"
- User wants to analyze a video file or extract frames
- User wants to extract text from screen (OCR)
- User wants to know what window they're in or where their cursor is
- Debugging UI issues — "does this look right?"
- Screen sharing / walkthroughs — "watch while I show you this feature"
- Video summarization — "what's in this video?"

## MCP Tools

### Screen Capture
- `capture_screen(delay_seconds, monitor, scale)` — Full screen capture. Use delay for window switching.
- `capture_region(x, y, width, height, scale)` — Capture a specific rectangular area.
- `capture_window(window_title, scale)` — Capture a window by title (e.g., "Chrome", "Terminal").
- `list_monitors()` — List all displays with resolutions and positions.
- `get_active_context()` — Lightweight window/cursor/monitor info without a screenshot.

### Analysis & OCR
- `read_screen_text(region)` — OCR on screen or region. Optional region as "x,y,width,height".
- `understand_screen(prompt)` — AI-powered screen analysis via GenAI Gateway.
- `analyze_image(file_path, prompt)` — Analyze any image file (e.g., AirDropped photo).

### Real-Time Watching
- `watch_screen(duration_seconds, interval_seconds, include_audio, max_frames)` — Watch screen with frame sampling + optional audio transcription via Whisper.
- `analyze_video(file_path, start_time, end_time, max_frames)` — Extract keyframes from local video files.

## Security

Screen Vision includes security controls:
- PII/PCI pattern detection in OCR text
- App deny-list (blocks Slack, Teams, Zoom, banking apps)
- Call detection (blocks captures during active calls)
- Rate limits (200 captures/session, 2s interval)
- Audit logs to `~/.screen-vision/audit.log`

## Optional System Dependencies

These are not required — tools gracefully degrade without them:
- **Tesseract** (OCR): `brew install tesseract`
- **FFmpeg** (video/audio): `brew install ffmpeg`

## Example Usage

```
User: "What's on my screen?"
Claude: [calls capture_screen()]

User: "Watch my screen while I demo this feature"
Claude: [calls watch_screen(duration_seconds=60, include_audio=true)]

User: "Analyze the video at ~/Downloads/demo.mp4"
Claude: [calls analyze_video(file_path="~/Downloads/demo.mp4")]

User: "Read the text on my screen"
Claude: [calls read_screen_text()]

User: "What window am I in?"
Claude: [calls get_active_context()]
```

## Tips

- Use `capture_screen` with delay for window switching: "take a screenshot in 5 seconds"
- Use `watch_screen` with audio for demos where user is talking
- Use `capture_region` for specific UI elements
- Use `read_screen_text` when user wants text extraction only (faster than full capture)
- Use `get_active_context` for lightweight queries (no image processing)
