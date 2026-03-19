"""MCP Server for Screen Vision - provides 8 tools for screen capture and analysis."""
import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from screen_vision.config import get_config
from screen_vision import context
from screen_vision.capture import ScreenCapture, encode_jpeg
from screen_vision.ocr import run_ocr, extract_text_near
from screen_vision.security import SecurityScanner
from screen_vision.watcher import ScreenWatcher
from screen_vision.video import analyze_video as analyze_video_func

# Initialize FastMCP server
mcp = FastMCP(
    "screen-vision",
    instructions="Screen Vision gives Claude the ability to see your screen."
)

# Rate limiting state
_session_captures = 0
_last_capture_time = 0.0
_capture: ScreenCapture | None = None


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
        scanner = SecurityScanner(enabled=True)

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
    # Check rate limit
    rate_err = _check_rate_limit()
    if rate_err:
        return json.dumps({
            "error": True,
            "code": "RATE_LIMITED",
            "message": rate_err
        })

    # Record capture
    _record_capture()

    # Capture screen
    cap = _get_capture()
    result = cap.capture_screen(
        delay_seconds=delay_seconds,
        monitor=monitor,
        scale=scale
    )

    # Process frame
    processed = _process_frame(result.image, result.cursor_position, result.active_window)

    return json.dumps(processed)


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
    # Check rate limit
    rate_err = _check_rate_limit()
    if rate_err:
        return json.dumps({
            "error": True,
            "code": "RATE_LIMITED",
            "message": rate_err
        })

    # Record capture
    _record_capture()

    # Capture region
    cap = _get_capture()
    result = cap.capture_region(x, y, width, height, scale=scale)

    # Process frame
    processed = _process_frame(result.image, result.cursor_position, result.active_window)

    return json.dumps(processed)


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
    # Check rate limit
    rate_err = _check_rate_limit()
    if rate_err:
        return json.dumps({
            "error": True,
            "code": "RATE_LIMITED",
            "message": rate_err
        })

    # Record capture
    _record_capture()

    # Capture window
    cap = _get_capture()
    result = cap.capture_window(window_title, scale=scale)

    # Process frame
    processed = _process_frame(result.image, result.cursor_position, result.active_window)

    return json.dumps(processed)


@mcp.tool()
async def list_monitors() -> str:
    """List available monitors.

    Returns:
        JSON string with monitor information
    """
    monitors = context.get_monitors()
    return json.dumps({"monitors": monitors})


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

    # Record capture
    _record_capture()

    # Create watcher and watch
    watcher = ScreenWatcher(
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
        include_audio=include_audio,
        max_frames=max_frames
    )

    result = watcher.watch()

    # Security scan keyframes in work mode
    security_redactions = 0
    if cfg.is_work_mode:
        scanner = SecurityScanner(enabled=True)
        clean_keyframes = []

        for keyframe in result.keyframes:
            # Check if OCR text contains sensitive data
            if keyframe.ocr_near_cursor:
                scan = scanner.scan_text(keyframe.ocr_near_cursor)
                if scan.should_block:
                    # Skip this frame
                    security_redactions += 1
                    continue
                security_redactions += len([f for f in scan.findings if f.action == "REDACT"])

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
    # Check rate limit
    rate_err = _check_rate_limit()
    if rate_err:
        return json.dumps({
            "error": True,
            "code": "RATE_LIMITED",
            "message": rate_err
        })

    # Record capture
    _record_capture()

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

    # Security scan frames in work mode
    cfg = get_config()
    security_redactions = 0
    if cfg.is_work_mode:
        scanner = SecurityScanner(enabled=True)
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
    # Check rate limit
    rate_err = _check_rate_limit()
    if rate_err:
        return json.dumps({
            "error": True,
            "code": "RATE_LIMITED",
            "message": rate_err
        })

    # Record capture
    _record_capture()

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
        scanner = SecurityScanner(enabled=True)
        scan = scanner.scan_text(text)

        if scan.should_block:
            return json.dumps({
                "error": True,
                "code": "SECURITY_BLOCKED",
                "message": "Text blocked: sensitive data detected"
            })

        security_redactions = len([f for f in scan.findings if f.action == "REDACT"])

    return json.dumps({
        "text": text,
        "average_confidence": ocr_result.average_confidence if ocr_result else 0.0,
        "security_redactions": security_redactions,
        "error": None,
    })


@mcp.tool()
async def get_active_context() -> str:
    """Get lightweight context: window, cursor, monitors.

    Returns:
        JSON string with context information
    """
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
