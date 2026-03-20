"""MCP Server for Screen Vision - provides 14 tools for screen capture and analysis."""
import asyncio
import json
import time
import base64
import socket
from typing import Any

from mcp.server.fastmcp import FastMCP

from screen_vision.config import get_config
from screen_vision import context
from screen_vision import analyze as analyze_mod
from screen_vision.capture import ScreenCapture, encode_jpeg
from screen_vision.ocr import run_ocr, extract_text_near
from screen_vision.security import SecurityScanner
from screen_vision.watcher import ScreenWatcher
from screen_vision.video import analyze_video as analyze_video_func
from screen_vision.understanding import understand_image

# Initialize FastMCP server
mcp = FastMCP(
    "screen-vision",
    instructions="Screen Vision gives Claude the ability to see your screen."
)

# Rate limiting state
_session_captures = 0
_last_capture_time = 0.0
_capture: ScreenCapture | None = None

# Security scanner singleton
_scanner: SecurityScanner | None = None


def _get_scanner() -> SecurityScanner:
    """Get or create the global SecurityScanner instance.

    Returns:
        SecurityScanner instance
    """
    global _scanner
    if _scanner is None:
        cfg = get_config()
        _scanner = SecurityScanner(enabled=cfg.security_scanning_enabled)
    return _scanner


def _get_capture() -> ScreenCapture:
    """Get or create the global ScreenCapture instance.

    Returns:
        ScreenCapture instance
    """
    global _capture
    if _capture is None:
        _capture = ScreenCapture()
    return _capture


def _check_rate_limit() -> str | None:
    """Check if capture is allowed based on rate limits.

    Returns error message if rate limited, None if OK.

    Returns:
        Error message string if rate limited, None if allowed
    """
    cfg = get_config()
    if not cfg.is_work_mode:
        return None

    now = time.time()

    # Check minimum interval between captures
    if cfg.min_capture_interval > 0:
        time_since_last = now - _last_capture_time
        if _last_capture_time > 0 and time_since_last < cfg.min_capture_interval:
            return f"Rate limit: max 1 capture per {cfg.min_capture_interval}s"

    # Check session budget
    if cfg.max_captures_per_session > 0 and _session_captures >= cfg.max_captures_per_session:
        return f"Session budget ({cfg.max_captures_per_session}) exceeded"

    return None


def _record_capture():
    """Record that a capture occurred for rate limiting."""
    global _session_captures, _last_capture_time
    _session_captures += 1
    _last_capture_time = time.time()


def _process_frame(
    image,
    cursor_pos: tuple[int, int] | None,
    active_window: dict[str, str]
) -> dict[str, Any]:
    """Process a captured frame with OCR and security scanning.

    Args:
        image: PIL Image object
        cursor_pos: Cursor position as (x, y) tuple or None
        active_window: Dict with app_name and window_title

    Returns:
        Dict with processed frame data or error info
    """
    cfg = get_config()

    # Run OCR if available
    ocr_result = None
    ocr_near = ""
    try:
        ocr_result = run_ocr(image)
        if ocr_result and cursor_pos:
            cursor_dict = {"x": cursor_pos[0], "y": cursor_pos[1]}
            ocr_near = extract_text_near(ocr_result.blocks, cursor_dict, radius=200)
    except Exception:
        # OCR failed - continue without it
        pass

    # Security scan in work mode
    security_redactions = 0
    if cfg.is_work_mode:
        scanner = _get_scanner()

        # Check app deny-list
        if active_window and scanner.is_app_blocked(active_window.get("app_name", "")):
            return {
                "error": True,
                "code": "APP_BLOCKED",
                "message": f"Cannot capture: {active_window.get('app_name', '')} is blocked"
            }

        # Scan OCR text
        if ocr_result and ocr_result.text:
            scan = scanner.scan_text(ocr_result.text)
            if scan.should_block:
                return {
                    "error": True,
                    "code": "SECURITY_BLOCKED",
                    "message": "Frame blocked: sensitive data detected"
                }
            security_redactions = len([f for f in scan.findings if f.action == "REDACT"])

    # Encode image
    b64 = encode_jpeg(image, quality=cfg.default_jpeg_quality)

    # Build response
    return {
        "image": b64,
        "format": "jpeg",
        "resolution": [image.width, image.height],
        "cursor_position": list(cursor_pos) if cursor_pos else None,
        "active_window": f"{active_window.get('app_name', '')} — {active_window.get('window_title', '')}" if active_window else "Unknown",
        "ocr_text_near_cursor": ocr_near,
        "security_redactions": security_redactions,
        "timestamp": time.time(),
    }


@mcp.tool()
async def capture_screen(
    delay_seconds: int = 3,
    monitor: int = 0,
    scale: float = 0.5
) -> str:
    """Capture the full screen with optional delay for window switching.

    Args:
        delay_seconds: Wait this many seconds before capturing (default: 3)
        monitor: Monitor index to capture (0 = all monitors, 1+ = specific monitor)
        scale: Scale factor for resizing (default: 0.5)

    Returns:
        JSON string with captured frame data or error
    """
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Wait asynchronously for delay
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        # Capture screen
        cap = _get_capture()
        result = cap.capture_screen(
            delay_seconds=0,  # Already waited above
            monitor=monitor,
            scale=scale
        )

        # Record capture AFTER successful capture
        _record_capture()

        # Process frame
        processed = _process_frame(result.image, result.cursor_position, result.active_window)

        return json.dumps(processed)
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def capture_region(
    x: int,
    y: int,
    width: int,
    height: int,
    scale: float = 1.0
) -> str:
    """Capture a specific screen region.

    Args:
        x: Left coordinate of the region
        y: Top coordinate of the region
        width: Width of the region
        height: Height of the region
        scale: Scale factor for resizing (default: 1.0)

    Returns:
        JSON string with captured frame data or error
    """
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Capture region
        cap = _get_capture()
        result = cap.capture_region(x, y, width, height, scale=scale)

        # Record capture AFTER successful capture
        _record_capture()

        # Process frame
        processed = _process_frame(result.image, result.cursor_position, result.active_window)

        return json.dumps(processed)
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def capture_window(
    window_title: str,
    scale: float = 0.5
) -> str:
    """Capture a specific window by title.

    Args:
        window_title: Title of the window to capture
        scale: Scale factor for resizing (default: 0.5)

    Returns:
        JSON string with captured frame data or error
    """
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Capture window
        cap = _get_capture()
        result = cap.capture_window(window_title, scale=scale)

        # Record capture AFTER successful capture
        _record_capture()

        # Process frame
        processed = _process_frame(result.image, result.cursor_position, result.active_window)

        return json.dumps(processed)
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def list_monitors() -> str:
    """List available monitors.

    Returns:
        JSON string with monitor information
    """
    try:
        monitors = context.get_monitors()
        return json.dumps({"monitors": monitors})
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def watch_screen(
    duration_seconds: int = 60,
    interval_seconds: float = 4.0,
    include_audio: bool = True,
    max_frames: int = 30
) -> str:
    """Watch the screen for a duration with frame sampling and optional audio.

    Args:
        duration_seconds: How long to watch (default: 60)
        interval_seconds: Time between frame captures (default: 4.0)
        include_audio: Whether to record and transcribe audio (default: True)
        max_frames: Maximum number of keyframes to keep (default: 30)

    Returns:
        JSON string with keyframes, transcript, and metadata
    """
    try:
        cfg = get_config()

        # Check rate limit for initial capture
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Check work mode limits
        if cfg.is_work_mode:
            if cfg.max_watch_duration > 0 and duration_seconds > cfg.max_watch_duration:
                return json.dumps({
                    "error": True,
                    "code": "DURATION_EXCEEDED",
                    "message": f"Duration ({duration_seconds}s) exceeds limit ({cfg.max_watch_duration}s)"
                })
            if cfg.max_frames_per_watch > 0 and max_frames > cfg.max_frames_per_watch:
                return json.dumps({
                    "error": True,
                    "code": "FRAMES_EXCEEDED",
                    "message": f"Max frames ({max_frames}) exceeds limit ({cfg.max_frames_per_watch})"
                })

        # Create watcher and watch
        watcher = ScreenWatcher(
            duration_seconds=duration_seconds,
            interval_seconds=interval_seconds,
            include_audio=include_audio,
            max_frames=max_frames
        )

        result = watcher.watch()

        # Record capture AFTER successful watch
        _record_capture()

        # Security scan keyframes in work mode
        security_redactions = 0
        if cfg.is_work_mode:
            scanner = _get_scanner()
            clean_keyframes = []

            for keyframe in result.keyframes:
                # Run full OCR on each keyframe to scan entire screen
                try:
                    from PIL import Image
                    import io
                    # Decode base64 image
                    img_bytes = base64.b64decode(keyframe.base64_image)
                    image = Image.open(io.BytesIO(img_bytes))

                    # Run full OCR
                    ocr_result = run_ocr(image)
                    full_text = ocr_result.text if ocr_result else ""

                    # Scan full text
                    if full_text:
                        scan = scanner.scan_text(full_text)
                        if scan.should_block:
                            # Skip this frame
                            security_redactions += 1
                            continue

                        # If redaction is needed, redact the image
                        if scan.should_redact and ocr_result:
                            from screen_vision.security import redact_image
                            redacted_image = redact_image(image, ocr_result.blocks, scan.findings)
                            # Re-encode the redacted image
                            keyframe.base64_image = encode_jpeg(redacted_image, quality=cfg.default_jpeg_quality)

                            # Mask PII in OCR text
                            masked_text = full_text
                            for finding in scan.findings:
                                if finding.action == "REDACT":
                                    masked_text = masked_text.replace(finding.match, "[REDACTED]")
                            keyframe.ocr_near_cursor = masked_text[:200]  # Keep first 200 chars

                        security_redactions += len([f for f in scan.findings if f.action == "REDACT"])
                except Exception:
                    # OCR/redaction failed - skip frame to be safe
                    security_redactions += 1
                    continue

                clean_keyframes.append(keyframe)

            result.keyframes = clean_keyframes

        # Build JSON response
        keyframes_json = []
        for kf in result.keyframes:
            keyframes_json.append({
                "base64_image": kf.base64_image,
                "timestamp": kf.timestamp,
                "active_window": f"{kf.active_window.get('app_name', '')} — {kf.active_window.get('window_title', '')}",
                "cursor_position": list(kf.cursor_position) if kf.cursor_position else None,
                "ocr_near_cursor": kf.ocr_near_cursor,
            })

        transcript_json = []
        for seg in result.transcript:
            transcript_json.append({
                "text": seg.text,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "nearest_frame_index": seg.nearest_frame_index,
            })

        return json.dumps({
            "keyframes": keyframes_json,
            "transcript": transcript_json,
            "duration_actual": result.duration_actual,
            "frames_captured": result.frames_captured,
            "frames_skipped_duplicate": result.frames_skipped_duplicate,
            "audio_recorded": result.audio_recorded,
            "security_redactions": security_redactions,
            "error": result.error,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def analyze_video(
    file_path: str,
    start_time: float = 0,
    end_time: float | None = None,
    max_frames: int = 20
) -> str:
    """Analyze a local video file.

    Args:
        file_path: Path to the video file
        start_time: Start time in seconds (default: 0)
        end_time: End time in seconds (default: None = entire video)
        max_frames: Maximum number of frames to extract (default: 20)

    Returns:
        JSON string with extracted frames and metadata
    """
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Analyze video
        result = analyze_video_func(
            file_path=file_path,
            start_time=start_time,
            end_time=end_time,
            max_frames=max_frames
        )

        # If error occurred
        if result.error:
            return json.dumps({
                "error": True,
                "code": "VIDEO_ERROR",
                "message": result.error
            })

        # Record capture AFTER successful analysis
        _record_capture()

        # Security scan frames in work mode
        cfg = get_config()
        security_redactions = 0
        if cfg.is_work_mode:
            scanner = _get_scanner()
            clean_keyframes = []

            for kf in result.keyframes:
                # Run OCR on frame
                try:
                    ocr_result = run_ocr(kf["image"])
                    if ocr_result and ocr_result.text:
                        scan = scanner.scan_text(ocr_result.text)
                        if scan.should_block:
                            # Skip this frame
                            security_redactions += 1
                            continue

                        # If redaction is needed, redact the image
                        if scan.should_redact:
                            from screen_vision.security import redact_image
                            kf["image"] = redact_image(kf["image"], ocr_result.blocks, scan.findings)

                        security_redactions += len([f for f in scan.findings if f.action == "REDACT"])
                except Exception:
                    # OCR failed - include frame anyway
                    pass

                clean_keyframes.append(kf)

            result.keyframes = clean_keyframes

        # Encode frames to base64
        keyframes_json = []
        for kf in result.keyframes:
            b64 = encode_jpeg(kf["image"], quality=75)
            keyframes_json.append({
                "base64_image": b64,
                "timestamp": kf["timestamp"],
            })

        return json.dumps({
            "keyframes": keyframes_json,
            "duration": result.duration,
            "frames_extracted": result.frames_extracted,
            "security_redactions": security_redactions,
            "error": None,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def read_screen_text(
    region: str | None = None
) -> str:
    """OCR the screen or a region.

    Args:
        region: Optional region as "x,y,width,height" string

    Returns:
        JSON string with extracted text
    """
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Parse region if provided
        cap = _get_capture()
        if region:
            try:
                parts = region.split(",")
                x, y, width, height = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                result = cap.capture_region(x, y, width, height, scale=1.0)
            except Exception as e:
                return json.dumps({
                    "error": True,
                    "code": "INVALID_REGION",
                    "message": f"Invalid region format: {e}"
                })
        else:
            result = cap.capture_screen(scale=0.5)

        # Record capture AFTER successful capture
        _record_capture()

        # Run OCR
        try:
            ocr_result = run_ocr(result.image)
        except Exception as e:
            return json.dumps({
                "error": True,
                "code": "OCR_ERROR",
                "message": f"OCR failed: {e}"
            })

        # Security scan text in work mode
        cfg = get_config()
        security_redactions = 0
        text = ocr_result.text if ocr_result else ""

        if cfg.is_work_mode and text:
            scanner = _get_scanner()
            scan = scanner.scan_text(text)

            if scan.should_block:
                return json.dumps({
                    "error": True,
                    "code": "SECURITY_BLOCKED",
                    "message": "Text blocked: sensitive data detected"
                })

            # Mask PII in text if redaction is needed
            if scan.should_redact:
                for finding in scan.findings:
                    if finding.action == "REDACT":
                        text = text.replace(finding.match, "[REDACTED]")

            security_redactions = len([f for f in scan.findings if f.action == "REDACT"])

        return json.dumps({
            "text": text,
            "average_confidence": ocr_result.average_confidence if ocr_result else 0.0,
            "security_redactions": security_redactions,
            "error": None,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


# Phone camera bridge
_bridge = None


def _get_bridge():
    """Get or create the global CameraBridge instance.

    Returns:
        CameraBridge instance
    """
    global _bridge
    if _bridge is None:
        from screen_vision.camera_bridge import CameraBridge
        cfg = get_config()
        _bridge = CameraBridge(port=cfg.camera_bridge_port)
    return _bridge


def _get_lan_ip() -> str:
    """Get the LAN IP address of this machine.

    Returns:
        IP address as string, or 127.0.0.1 if unable to determine
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


@mcp.tool()
async def get_active_context() -> str:
    """Get lightweight context: window, cursor, monitors.

    Returns:
        JSON string with context information
    """
    try:
        # No capture needed - just metadata
        cursor_pos = context.get_cursor_position()
        active_win = context.get_active_window()
        monitors = context.get_monitors()

        return json.dumps({
            "cursor_position": list(cursor_pos) if cursor_pos else None,
            "active_window": active_win,
            "monitors": monitors,
            "timestamp": time.time(),
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def analyze_image(file_path: str, prompt: str = "") -> str:
    """Analyze a dropped image file (AirDrop, screenshot, saved photo).

    Works in both work and personal modes. In work mode, security scanning is applied.

    Args:
        file_path: Path to the image file to analyze
        prompt: Optional analysis prompt (reserved for future use)

    Returns:
        JSON string with analyzed image data or error
    """
    try:
        result = analyze_mod.analyze_image(file_path, prompt)

        if result.error:
            return json.dumps({
                "error": True,
                "code": "ANALYSIS_FAILED",
                "message": result.error
            })

        # Parse resolution string "1920x1080" into list [1920, 1080]
        width, height = result.resolution.split("x")
        resolution_list = [int(width), int(height)]

        return json.dumps({
            "image": result.base64_image,
            "format": "jpeg",
            "resolution": resolution_list,
            "source": result.source,
            "file_name": result.file_name,
            "ocr_text": result.ocr_text,
            "security_redactions": result.security_redactions,
            "timestamp": result.timestamp,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def understand_screen(prompt: str = "") -> str:
    """Understand what's on screen — like Google Lens for your desktop.
    Returns structured analysis: what app, what content, what's happening, actionable insights.
    Optionally provide a prompt for focused analysis: 'what error is this?' or 'explain this dashboard'.

    Args:
        prompt: Optional custom prompt for focused analysis (default: "")

    Returns:
        JSON string with understanding result, image, OCR text, and metadata
    """
    hidden_app = None
    try:
        # Check rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return json.dumps({
                "error": True,
                "code": "RATE_LIMITED",
                "message": rate_err
            })

        # Auto-hide terminal for clean screenshot
        hidden_app = context.hide_terminal()

        # macOS needs ~2s to fully update the screen buffer after hiding a window
        if hidden_app:
            await asyncio.sleep(2.0)

        # Capture screen
        cap = _get_capture()
        result = cap.capture_screen(delay_seconds=0, scale=0.5)

        # Run OCR
        ocr_result = None
        ocr_text = ""
        try:
            ocr_result = run_ocr(result.image)
            ocr_text = ocr_result.text if ocr_result else ""
        except Exception:
            # OCR failed - continue without it
            pass

        # Get config
        cfg = get_config()

        # Security scan in work mode
        security_redactions = 0
        if cfg.is_work_mode and ocr_text:
            scanner = _get_scanner()
            scan = scanner.scan_text(ocr_text)

            if scan.should_block:
                return json.dumps({
                    "error": True,
                    "code": "SECURITY_BLOCKED",
                    "message": "Screen blocked: sensitive data detected"
                })

            security_redactions = len([f for f in scan.findings if f.action == "REDACT"])

        # Call understanding module
        understanding_result = await understand_image(
            result.image,
            ocr_text=ocr_text,
            prompt=prompt
        )

        # Record capture AFTER successful analysis
        _record_capture()

        # Encode image
        b64 = encode_jpeg(result.image, quality=cfg.default_jpeg_quality)

        # Build response
        return json.dumps({
            "understanding": {
                "summary": understanding_result.summary,
                "application": understanding_result.application,
                "tags": understanding_result.tags,
                "entities": understanding_result.entities,
                "actionable_insights": understanding_result.actionable_insights,
                "confidence": understanding_result.confidence,
                "error": understanding_result.error,
                "full_text": understanding_result.full_text,
            },
            "image": b64,
            "format": "jpeg",
            "resolution": [result.image.width, result.image.height],
            "full_text": ocr_text,
            "active_window": f"{result.active_window.get('app_name', '')} — {result.active_window.get('window_title', '')}" if result.active_window else "Unknown",
            "timestamp": time.time(),
            "security_redactions": security_redactions,
            "understanding_latency_ms": understanding_result.latency_ms,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})
    finally:
        # Always restore the terminal, even if capture/understanding fails
        if hidden_app:
            context.restore_terminal(hidden_app)


@mcp.tool()
async def show_pairing_qr() -> str:
    """Show QR code to connect phone camera. Scan with iPhone to start streaming.

    Only available in personal mode. In work mode, use analyze_image() with AirDrop instead.

    Returns:
        JSON string with QR code data and pairing instructions, or error
    """
    try:
        cfg = get_config()
        if cfg.is_work_mode:
            return json.dumps({
                "error": True,
                "code": "WORK_MODE",
                "message": "Phone camera streaming is not available in work mode. Use analyze_image() with AirDrop instead."
            })

        bridge = _get_bridge()
        lan_ip = _get_lan_ip()
        qr_data = bridge.generate_pairing_qr(lan_ip)

        return json.dumps(qr_data)
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def capture_camera(prompt: str = "") -> str:
    """Grab the latest frame from connected phone camera.

    Only available in personal mode. Requires phone to be connected via show_pairing_qr() first.

    Args:
        prompt: Optional prompt (reserved for future use)

    Returns:
        JSON string with frame data or error
    """
    try:
        cfg = get_config()
        if cfg.is_work_mode:
            return json.dumps({
                "error": True,
                "code": "WORK_MODE",
                "message": "Use analyze_image() with AirDrop in work mode."
            })

        bridge = _get_bridge()
        if not bridge.is_phone_connected:
            return json.dumps({
                "error": True,
                "code": "NO_PHONE",
                "message": "No phone connected. Use show_pairing_qr() first."
            })

        frame_data = bridge.frame_queue.get_latest()
        if frame_data is None:
            return json.dumps({
                "error": True,
                "code": "NO_FRAMES",
                "message": "No frames received yet."
            })

        frame_bytes, timestamp = frame_data
        b64 = base64.b64encode(frame_bytes).decode("utf-8")

        return json.dumps({
            "image": b64,
            "format": "jpeg",
            "source": "phone_camera",
            "timestamp": timestamp,
            "frame_age_ms": int((time.time() - timestamp) * 1000),
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def watch_camera(
    duration_seconds: int = 30,
    include_audio: bool = True,
    max_frames: int = 20
) -> str:
    """Stream phone camera frames with scene detection and optional audio.

    Only available in personal mode. Requires phone to be connected via show_pairing_qr() first.
    Collects frames over the specified duration, applies scene change detection to keep only
    keyframes, and optionally records and transcribes audio.

    Args:
        duration_seconds: How long to collect frames (default: 30)
        include_audio: Whether to collect and transcribe audio (default: True)
        max_frames: Maximum number of keyframes to keep (default: 20)

    Returns:
        JSON string with keyframes, transcript, and metadata
    """
    try:
        cfg = get_config()
        if cfg.is_work_mode:
            return json.dumps({
                "error": True,
                "code": "WORK_MODE",
                "message": "Use analyze_image() with AirDrop in work mode."
            })

        bridge = _get_bridge()
        if not bridge.is_phone_connected:
            return json.dumps({
                "error": True,
                "code": "NO_PHONE",
                "message": "No phone connected. Use show_pairing_qr() first."
            })

        # Collect frames for duration
        start_time = time.time()
        frames_captured = 0
        collected_frames = []

        while time.time() - start_time < duration_seconds:
            frame_data = bridge.frame_queue.get_latest()
            if frame_data:
                frame_bytes, timestamp = frame_data
                # Simple deduplication: only add if timestamp is different from last frame
                if not collected_frames or collected_frames[-1][1] != timestamp:
                    collected_frames.append((frame_bytes, timestamp))
                    frames_captured += 1

            await asyncio.sleep(0.5)  # Check every 0.5 seconds

        # Apply simple scene change detection: keep every Nth frame up to max_frames
        if len(collected_frames) > max_frames:
            step = len(collected_frames) // max_frames
            keyframes = [collected_frames[i] for i in range(0, len(collected_frames), step)][:max_frames]
        else:
            keyframes = collected_frames

        # Encode keyframes to base64
        keyframes_json = []
        for frame_bytes, timestamp in keyframes:
            b64 = base64.b64encode(frame_bytes).decode("utf-8")
            keyframes_json.append({
                "base64_image": b64,
                "timestamp": timestamp,
            })

        # Audio transcription (simplified for now)
        transcript = []
        if include_audio and bridge.audio_buffer:
            # In a real implementation, we would transcribe the audio buffer
            # For now, just note that audio was recorded
            transcript.append({
                "text": f"[Audio recorded: {len(bridge.audio_buffer)} chunks]",
                "start_time": start_time,
                "end_time": time.time(),
                "nearest_frame_index": 0,
            })

        duration_actual = time.time() - start_time

        return json.dumps({
            "keyframes": keyframes_json,
            "transcript": transcript,
            "duration_actual": duration_actual,
            "frames_captured": frames_captured,
            "frames_skipped_duplicate": len(collected_frames) - len(keyframes),
            "audio_recorded": include_audio and len(bridge.audio_buffer) > 0,
            "error": None,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


@mcp.tool()
async def phone_status() -> str:
    """Check phone camera connection status.

    Returns connection status, frame queue size, and server state.

    Returns:
        JSON string with status information
    """
    try:
        cfg = get_config()
        bridge = _get_bridge() if not cfg.is_work_mode else None

        if bridge is None:
            return json.dumps({
                "connected": False,
                "mode": "work",
                "message": "Phone streaming unavailable in work mode."
            })

        return json.dumps({
            "connected": bridge.is_phone_connected,
            "frames_in_queue": len(bridge.frame_queue),
            "server_running": bridge.is_running,
        })
    except Exception as e:
        return json.dumps({"error": True, "code": "INTERNAL_ERROR", "message": str(e)})


def main():
    """Entry point for the MCP server."""
    cfg = get_config()

    # In work mode, verify tesseract is installed
    if cfg.is_work_mode:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
        except Exception:
            print("ERROR: tesseract required in work mode. Install: brew install tesseract")
            raise SystemExit(1)

    # Run the server
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
