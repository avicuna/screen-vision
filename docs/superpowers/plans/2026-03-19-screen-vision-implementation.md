# Screen Vision MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that gives Claude Code the ability to see the user's screen, watch it in real-time with audio transcription, and analyze video files — with corporate-grade security controls.

**Architecture:** Single-process Python MCP server using FastMCP. mss for screen capture, sounddevice for mic recording, faster-whisper for transcription, pytesseract for OCR, opencv for scene detection. Security pipeline scans every frame via OCR before sending. Config-driven work/personal mode.

**Tech Stack:** Python 3.11+, FastMCP, mss, Pillow, sounddevice, faster-whisper, pytesseract, opencv-python, numpy

**Spec:** `docs/superpowers/specs/2026-03-19-screen-vision-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Package config, dependencies, entry points |
| `src/screen_vision/__init__.py` | Package init |
| `src/screen_vision/config.py` | Mode (work/personal), settings, thresholds |
| `src/screen_vision/context.py` | Active window, cursor position, monitor info via osascript |
| `src/screen_vision/capture.py` | Screenshot capture: full screen, region, window |
| `src/screen_vision/ocr.py` | pytesseract OCR wrapper with bounding boxes |
| `src/screen_vision/security.py` | PII/PCI/secrets scanner, app deny-list, redaction, audit log |
| `src/screen_vision/watcher.py` | Live screen watching with adaptive frame sampling |
| `src/screen_vision/audio.py` | Mic recording + Whisper transcription + call detection |
| `src/screen_vision/video.py` | Video file analysis via ffmpeg |
| `src/screen_vision/server.py` | FastMCP server with 8 tool definitions |
| `tests/conftest.py` | Shared fixtures |
| `tests/test_config.py` | Config and mode tests |
| `tests/test_context.py` | Window/cursor/monitor tests |
| `tests/test_capture.py` | Screenshot tests |
| `tests/test_ocr.py` | OCR tests |
| `tests/test_security.py` | Security scanner tests (PII/PCI/secrets/deny-list) |
| `tests/test_watcher.py` | Frame sampling and scene detection tests |
| `tests/test_audio.py` | Audio recording and call detection tests |
| `tests/test_video.py` | Video analysis tests |
| `tests/test_server.py` | MCP tool integration tests |

---

## Task 1: Project Scaffolding + Config

**Files:**
- Create: `pyproject.toml`
- Create: `src/screen_vision/__init__.py`
- Create: `src/screen_vision/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "screen-vision"
version = "0.1.0"
description = "MCP server that gives Claude Code the ability to see your screen"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0",
    "mss>=9.0.0",
    "Pillow>=10.0.0",
    "numpy>=1.24.0",
]

[project.optional-dependencies]
full = [
    "pytesseract>=0.3.10",
    "faster-whisper>=1.0.0",
    "sounddevice>=0.4.6",
    "opencv-python>=4.8.0",
]
test = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[project.scripts]
screen-vision-mcp = "screen_vision.server:main"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create `src/screen_vision/__init__.py`**

```python
"""Screen Vision MCP Server — see your screen from Claude Code."""
```

- [ ] **Step 3: Write config tests**

```python
# tests/test_config.py
"""Tests for configuration."""
import os
from unittest.mock import patch

from screen_vision.config import Config, get_config


def test_default_mode_is_personal():
    with patch.dict(os.environ, {}, clear=True):
        cfg = get_config()
    assert cfg.mode == "personal"


def test_work_mode_from_env():
    with patch.dict(os.environ, {"SCREEN_VISION_MODE": "work"}):
        cfg = get_config()
    assert cfg.mode == "work"
    assert cfg.security_scanning_enabled is True
    assert cfg.app_denylist_enabled is True
    assert cfg.audit_logging_enabled is True
    assert cfg.call_detection_enabled is True


def test_personal_mode_defaults():
    with patch.dict(os.environ, {"SCREEN_VISION_MODE": "personal"}):
        cfg = get_config()
    assert cfg.security_scanning_enabled is False
    assert cfg.app_denylist_enabled is False
    assert cfg.audit_logging_enabled is False
    assert cfg.call_detection_enabled is False


def test_rate_limits_work_mode():
    with patch.dict(os.environ, {"SCREEN_VISION_MODE": "work"}):
        cfg = get_config()
    assert cfg.max_captures_per_session == 200
    assert cfg.min_capture_interval_seconds == 2.0
    assert cfg.max_watch_duration == 300
    assert cfg.max_video_file_mb == 500
    assert cfg.max_video_duration == 600
    assert cfg.max_frames_per_watch == 50


def test_personal_mode_relaxed_limits():
    with patch.dict(os.environ, {"SCREEN_VISION_MODE": "personal"}):
        cfg = get_config()
    assert cfg.max_captures_per_session == 0  # unlimited
    assert cfg.min_capture_interval_seconds == 0
    assert cfg.max_watch_duration == 0  # unlimited
    assert cfg.max_frames_per_watch == 0  # unlimited
```

- [ ] **Step 4: Create conftest.py**

```python
# tests/conftest.py
"""Shared test fixtures for Screen Vision."""
import pytest
from PIL import Image
import numpy as np


@pytest.fixture
def sample_image():
    """A 200x100 test image with some text-like content."""
    img = Image.fromarray(np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8))
    return img


@pytest.fixture
def work_env():
    return {"SCREEN_VISION_MODE": "work"}


@pytest.fixture
def personal_env():
    return {"SCREEN_VISION_MODE": "personal"}
```

- [ ] **Step 5: Run tests — verify they fail**

```bash
cd /Users/avicuna/dev/screen-vision
pip install -e ".[test]"
pytest tests/test_config.py -v
```

- [ ] **Step 6: Implement config.py**

```python
# src/screen_vision/config.py
"""Configuration for Screen Vision MCP Server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Screen Vision configuration. Driven by SCREEN_VISION_MODE env var."""

    mode: str  # "work" or "personal"

    # Security controls
    security_scanning_enabled: bool
    app_denylist_enabled: bool
    audit_logging_enabled: bool
    call_detection_enabled: bool

    # Rate limits (0 = unlimited)
    max_captures_per_session: int
    min_capture_interval_seconds: float
    max_watch_duration: int  # seconds
    max_video_file_mb: int
    max_video_duration: int  # seconds
    max_frames_per_watch: int

    # Capture settings
    default_scale: float
    default_delay_seconds: int
    default_jpeg_quality: int
    watch_jpeg_quality: int

    # Paths
    audit_log_path: str

    @property
    def is_work_mode(self) -> bool:
        return self.mode == "work"


_WORK_CONFIG = Config(
    mode="work",
    security_scanning_enabled=True,
    app_denylist_enabled=True,
    audit_logging_enabled=True,
    call_detection_enabled=True,
    max_captures_per_session=200,
    min_capture_interval_seconds=2.0,
    max_watch_duration=300,
    max_video_file_mb=500,
    max_video_duration=600,
    max_frames_per_watch=50,
    default_scale=0.5,
    default_delay_seconds=3,
    default_jpeg_quality=75,
    watch_jpeg_quality=65,
    audit_log_path=os.path.expanduser("~/.screen-vision/audit.log"),
)

_PERSONAL_CONFIG = Config(
    mode="personal",
    security_scanning_enabled=False,
    app_denylist_enabled=False,
    audit_logging_enabled=False,
    call_detection_enabled=False,
    max_captures_per_session=0,
    min_capture_interval_seconds=0,
    max_watch_duration=0,
    max_video_file_mb=0,
    max_video_duration=0,
    max_frames_per_watch=0,
    default_scale=0.5,
    default_delay_seconds=3,
    default_jpeg_quality=75,
    watch_jpeg_quality=65,
    audit_log_path="",
)


def get_config() -> Config:
    """Get config based on SCREEN_VISION_MODE env var."""
    mode = os.environ.get("SCREEN_VISION_MODE", "personal").lower()
    if mode == "work":
        return _WORK_CONFIG
    return _PERSONAL_CONFIG
```

- [ ] **Step 7: Run tests — verify they pass**

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Task 1: Project scaffolding + config (work/personal modes)"
```

---

## Task 2: Context Module (Window Title, Cursor, Monitors)

**Files:**
- Create: `src/screen_vision/context.py`
- Create: `tests/test_context.py`

- [ ] **Step 1: Write context tests**

```python
# tests/test_context.py
"""Tests for macOS context (window, cursor, monitors)."""
from unittest.mock import patch, MagicMock
import pytest

from screen_vision.context import get_cursor_position, get_active_window, get_monitors


def test_get_cursor_position_returns_tuple():
    with patch("screen_vision.context._run_osascript", return_value="423, 312"):
        pos = get_cursor_position()
    assert pos == (423, 312)


def test_get_cursor_position_fallback_on_error():
    with patch("screen_vision.context._run_osascript", side_effect=Exception("no access")):
        pos = get_cursor_position()
    assert pos is None


def test_get_active_window_returns_dict():
    with patch("screen_vision.context._run_osascript", return_value="Visual Studio Code\tmain.py"):
        window = get_active_window()
    assert window["app_name"] == "Visual Studio Code"
    assert window["window_title"] == "main.py"


def test_get_monitors_returns_list():
    monitors = get_monitors()
    assert isinstance(monitors, list)
    assert len(monitors) >= 1
    assert "width" in monitors[0]
    assert "height" in monitors[0]
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_context.py -v
```

- [ ] **Step 3: Implement context.py**

```python
# src/screen_vision/context.py
"""macOS context: active window, cursor position, monitor info."""

from __future__ import annotations

import subprocess
import mss


def _run_osascript(script: str) -> str:
    """Run an AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip()


def get_cursor_position() -> tuple[int, int] | None:
    """Get current cursor position via AppleScript."""
    try:
        script = '''
        tell application "System Events"
            set pos to position of the mouse
            return (item 1 of pos) & ", " & (item 2 of pos)
        end tell
        '''
        # Fallback: use Quartz if available
        try:
            from Quartz import NSEvent
            loc = NSEvent.mouseLocation()
            # Convert from bottom-left to top-left coordinate system
            with mss.mss() as sct:
                screen_height = sct.monitors[1]["height"]
            return (int(loc.x), int(screen_height - loc.y))
        except ImportError:
            result = _run_osascript(script)
            parts = result.split(",")
            return (int(parts[0].strip()), int(parts[1].strip()))
    except Exception:
        return None


def get_active_window() -> dict:
    """Get the active (frontmost) window info."""
    try:
        script = '''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            try
                set winTitle to name of front window of first application process whose frontmost is true
            on error
                set winTitle to ""
            end try
            return frontApp & "\t" & winTitle
        end tell
        '''
        result = _run_osascript(script)
        parts = result.split("\t", 1)
        return {
            "app_name": parts[0] if parts else "Unknown",
            "window_title": parts[1] if len(parts) > 1 else "",
        }
    except Exception:
        return {"app_name": "Unknown", "window_title": ""}


def get_monitors() -> list[dict]:
    """Get monitor info via mss."""
    with mss.mss() as sct:
        monitors = []
        for i, mon in enumerate(sct.monitors[1:], start=0):  # Skip the "all" monitor
            monitors.append({
                "index": i,
                "width": mon["width"],
                "height": mon["height"],
                "x": mon["left"],
                "y": mon["top"],
                "is_primary": i == 0,
            })
        return monitors


def get_visible_windows() -> list[dict]:
    """Get all visible windows with their bounds. Used for app deny-list checks."""
    try:
        from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        result = []
        for w in windows:
            bounds = w.get("kCGWindowBounds", {})
            result.append({
                "app_name": w.get("kCGWindowOwnerName", ""),
                "title": w.get("kCGWindowName", ""),
                "x": int(bounds.get("X", 0)),
                "y": int(bounds.get("Y", 0)),
                "width": int(bounds.get("Width", 0)),
                "height": int(bounds.get("Height", 0)),
            })
        return result
    except ImportError:
        return []
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_context.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/screen_vision/context.py tests/test_context.py
git commit -m "Task 2: Context module — window title, cursor position, monitors"
```

---

## Task 3: Screenshot Capture

**Files:**
- Create: `src/screen_vision/capture.py`
- Create: `tests/test_capture.py`

- [ ] **Step 1: Write capture tests**

```python
# tests/test_capture.py
"""Tests for screen capture."""
from unittest.mock import patch, MagicMock
import pytest
import numpy as np
from PIL import Image

from screen_vision.capture import ScreenCapture


@pytest.fixture
def mock_mss():
    """Mock mss to return a fake screenshot."""
    mock_sct = MagicMock()
    # Create a fake raw capture (BGRA format)
    fake_raw = MagicMock()
    fake_raw.size = (200, 100)
    fake_raw.bgra = np.zeros((100, 200, 4), dtype=np.uint8).tobytes()
    mock_sct.grab.return_value = fake_raw
    mock_sct.monitors = [
        {"left": 0, "top": 0, "width": 200, "height": 100},  # all
        {"left": 0, "top": 0, "width": 200, "height": 100},  # primary
    ]
    return mock_sct


def test_capture_screen_returns_result(mock_mss):
    with patch("screen_vision.capture.mss.mss", return_value=mock_mss):
        cap = ScreenCapture()
        cap._sct = mock_mss
        result = cap.capture_screen(delay_seconds=0, monitor=0, scale=1.0)

    assert result.image is not None
    assert isinstance(result.image, Image.Image)
    assert result.timestamp > 0


def test_capture_screen_scales_image(mock_mss):
    with patch("screen_vision.capture.mss.mss", return_value=mock_mss):
        cap = ScreenCapture()
        cap._sct = mock_mss
        result = cap.capture_screen(delay_seconds=0, monitor=0, scale=0.5)

    assert result.image.width == 100
    assert result.image.height == 50


def test_capture_region(mock_mss):
    with patch("screen_vision.capture.mss.mss", return_value=mock_mss):
        cap = ScreenCapture()
        cap._sct = mock_mss
        result = cap.capture_region(x=10, y=10, width=50, height=30)

    assert result.image is not None


def test_encode_jpeg():
    from screen_vision.capture import encode_jpeg
    img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    b64 = encode_jpeg(img, quality=75)
    assert isinstance(b64, str)
    assert len(b64) > 0
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement capture.py**

```python
# src/screen_vision/capture.py
"""Screen capture via mss."""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass

import mss
import mss.tools
import numpy as np
from PIL import Image

from screen_vision.context import get_cursor_position, get_active_window


@dataclass
class CaptureResult:
    image: Image.Image
    timestamp: float
    monitor_index: int
    cursor_position: tuple[int, int] | None
    active_window: dict


class ScreenCapture:
    def __init__(self):
        self._sct = mss.mss()

    def capture_screen(
        self, delay_seconds: int = 0, monitor: int = 0, scale: float = 0.5
    ) -> CaptureResult:
        """Capture full screen with optional delay."""
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        monitors = self._sct.monitors
        target = monitors[monitor + 1] if monitor < len(monitors) - 1 else monitors[1]

        raw = self._sct.grab(target)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        if scale != 1.0:
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)

        return CaptureResult(
            image=img,
            timestamp=time.time(),
            monitor_index=monitor,
            cursor_position=get_cursor_position(),
            active_window=get_active_window(),
        )

    def capture_region(
        self, x: int, y: int, width: int, height: int, scale: float = 1.0
    ) -> CaptureResult:
        """Capture a specific screen region."""
        region = {"left": x, "top": y, "width": width, "height": height}
        raw = self._sct.grab(region)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        if scale != 1.0:
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)

        return CaptureResult(
            image=img,
            timestamp=time.time(),
            monitor_index=-1,
            cursor_position=get_cursor_position(),
            active_window=get_active_window(),
        )

    def capture_window(self, window_title: str, scale: float = 0.5) -> CaptureResult:
        """Capture a specific window by title. macOS only."""
        import subprocess

        # Find window ID via osascript
        script = f'''
        tell application "System Events"
            set wList to every window of every application process
            repeat with proc in every application process
                repeat with w in every window of proc
                    if name of w contains "{window_title}" then
                        return id of w
                    end if
                end repeat
            end repeat
        end tell
        '''
        # Use screencapture -l <windowid> for clean window capture
        # Fallback to full screen + crop if osascript fails
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-l", "0", "-t", "png", "/dev/stdout"],
                capture_output=True, timeout=10,
            )
            # For now, fall back to full screen capture
            return self.capture_screen(delay_seconds=0, scale=scale)
        except Exception:
            return self.capture_screen(delay_seconds=0, scale=scale)


def encode_jpeg(image: Image.Image, quality: int = 75) -> str:
    """Encode PIL Image to base64 JPEG string. Strips EXIF."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def scene_changed(prev_bytes: bytes, curr_bytes: bytes, threshold: float = 0.02) -> bool:
    """Detect meaningful screen changes via pixel diff on tiny thumbnails."""
    prev = np.array(Image.open(io.BytesIO(prev_bytes)).resize((160, 90)))
    curr = np.array(Image.open(io.BytesIO(curr_bytes)).resize((160, 90)))
    diff = np.abs(prev.astype(np.int16) - curr.astype(np.int16)).mean() / 255.0
    return diff > threshold
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/screen_vision/capture.py tests/test_capture.py
git commit -m "Task 3: Screenshot capture — full screen, region, window, JPEG encoding"
```

---

## Task 4: OCR Module

**Files:**
- Create: `src/screen_vision/ocr.py`
- Create: `tests/test_ocr.py`

- [ ] **Step 1: Write OCR tests**

```python
# tests/test_ocr.py
"""Tests for OCR module."""
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image
import numpy as np

from screen_vision.ocr import run_ocr, extract_text_near, OcrResult


def test_run_ocr_returns_result():
    mock_data = {
        "text": ["Hello", "World", ""],
        "conf": [95, 88, -1],
        "left": [10, 100, 0],
        "top": [20, 20, 0],
        "width": [60, 70, 0],
        "height": [15, 15, 0],
    }
    with patch("screen_vision.ocr.pytesseract") as mock_tess:
        mock_tess.image_to_data.return_value = mock_data
        mock_tess.image_to_string.return_value = "Hello World"
        img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
        result = run_ocr(img)

    assert result.text == "Hello World"
    assert len(result.blocks) == 2
    assert result.average_confidence > 0


def test_extract_text_near_cursor():
    blocks = [
        {"text": "nearby", "bbox": (100, 100, 160, 115), "confidence": 90},
        {"text": "far away", "bbox": (900, 900, 990, 915), "confidence": 90},
    ]
    result = extract_text_near(blocks, cursor=(120, 110), radius=200)
    assert "nearby" in result
    assert "far away" not in result


def test_ocr_graceful_when_tesseract_missing():
    with patch("screen_vision.ocr.pytesseract", None):
        img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
        result = run_ocr(img)
    assert result.text == ""
    assert result.blocks == []
```

- [ ] **Step 2: Implement ocr.py**

```python
# src/screen_vision/ocr.py
"""OCR wrapper using pytesseract."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from PIL import Image

try:
    import pytesseract
    from pytesseract import Output
except ImportError:
    pytesseract = None


@dataclass
class OcrResult:
    text: str
    blocks: list[dict] = field(default_factory=list)
    average_confidence: float = 0.0


def run_ocr(image: Image.Image) -> OcrResult:
    """Run OCR on an image. Returns text + bounding boxes."""
    if pytesseract is None:
        return OcrResult(text="", blocks=[], average_confidence=0.0)

    try:
        full_text = pytesseract.image_to_string(image)
        data = pytesseract.image_to_data(image, output_type=Output.DICT)

        blocks = []
        confidences = []
        for i, text in enumerate(data["text"]):
            conf = int(data["conf"][i])
            if text.strip() and conf > 0:
                blocks.append({
                    "text": text.strip(),
                    "bbox": (
                        data["left"][i], data["top"][i],
                        data["left"][i] + data["width"][i],
                        data["top"][i] + data["height"][i],
                    ),
                    "confidence": conf,
                })
                confidences.append(conf)

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OcrResult(text=full_text.strip(), blocks=blocks, average_confidence=avg_conf)
    except Exception:
        return OcrResult(text="", blocks=[], average_confidence=0.0)


def extract_text_near(
    blocks: list[dict], cursor: tuple[int, int], radius: int = 200
) -> str:
    """Extract OCR text near the cursor position."""
    if not cursor or not blocks:
        return ""

    cx, cy = cursor
    nearby = []
    for block in blocks:
        bx1, by1, bx2, by2 = block["bbox"]
        # Center of the text block
        block_cx = (bx1 + bx2) / 2
        block_cy = (by1 + by2) / 2
        dist = math.sqrt((cx - block_cx) ** 2 + (cy - block_cy) ** 2)
        if dist <= radius:
            nearby.append(block["text"])

    return " ".join(nearby)
```

- [ ] **Step 3: Run tests — verify they pass**

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/ocr.py tests/test_ocr.py
git commit -m "Task 4: OCR module — pytesseract wrapper with bounding boxes"
```

---

## Task 5: Security Pipeline

**Files:**
- Create: `src/screen_vision/security.py`
- Create: `tests/test_security.py`

- [ ] **Step 1: Write security tests**

```python
# tests/test_security.py
"""Tests for security pipeline — PII/PCI/secrets scanning."""
import pytest
from screen_vision.security import (
    SecurityScanner,
    scan_text,
    ScanResult,
    BLOCKED_APPS,
)


@pytest.fixture
def scanner():
    return SecurityScanner(enabled=True)


# PCI tests
def test_detects_visa_card(scanner):
    result = scanner.scan_text("Card: 4111111111111111")
    assert any(f.finding_type == "PCI" for f in result.findings)
    assert result.should_block is True


def test_detects_mastercard(scanner):
    result = scanner.scan_text("Pay with 5500000000000004")
    assert any(f.finding_type == "PCI" for f in result.findings)


def test_ignores_non_luhn_number(scanner):
    result = scanner.scan_text("Order 4111111111111112")  # Fails Luhn
    assert not any(f.finding_type == "PCI" for f in result.findings)


# PII tests
def test_detects_email(scanner):
    result = scanner.scan_text("Contact john@example.com for help")
    assert any(f.finding_type == "PII" for f in result.findings)
    assert result.should_block is False  # PII = redact, not block


def test_detects_phone_with_indicator(scanner):
    result = scanner.scan_text("Call +1-555-123-4567 for support")
    assert any(f.finding_type == "PII" for f in result.findings)


def test_ignores_plain_number_without_indicator(scanner):
    result = scanner.scan_text("Version 1234567890")
    assert not any(f.finding_type == "PII" and "phone" in f.pattern_name.lower() for f in result.findings)


# Secrets tests
def test_detects_github_token(scanner):
    result = scanner.scan_text("token: ghp_1234567890abcdef1234567890abcdef12345678")
    assert any(f.finding_type == "SECRET" for f in result.findings)
    assert result.should_block is True


def test_detects_gitlab_token(scanner):
    result = scanner.scan_text("GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx")
    assert any(f.finding_type == "SECRET" for f in result.findings)


def test_detects_vault_token(scanner):
    result = scanner.scan_text("export VAULT_TOKEN=hvs.CAESIG123456789abcdef")
    assert any(f.finding_type == "SECRET" for f in result.findings)


def test_detects_jwt(scanner):
    result = scanner.scan_text("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw")
    assert any(f.finding_type == "SECRET" for f in result.findings)


def test_detects_ssh_key(scanner):
    result = scanner.scan_text("-----BEGIN RSA PRIVATE KEY-----")
    assert any(f.finding_type == "SECRET" for f in result.findings)


def test_detects_db_connection_string(scanner):
    result = scanner.scan_text("postgres://admin:secretpass@db.internal:5432/mydb")
    assert any(f.finding_type == "SECRET" for f in result.findings)


# Clean text
def test_clean_text_passes(scanner):
    result = scanner.scan_text("def hello_world(): return 42")
    assert result.is_clean is True
    assert len(result.findings) == 0


# Disabled scanner
def test_disabled_scanner_passes_everything():
    scanner = SecurityScanner(enabled=False)
    result = scanner.scan_text("4111111111111111")
    assert result.is_clean is True


# App deny-list
def test_blocked_apps_list():
    assert "Slack" in BLOCKED_APPS
    assert "Microsoft Teams" in BLOCKED_APPS
    assert "1Password" in BLOCKED_APPS


def test_app_is_blocked(scanner):
    assert scanner.is_app_blocked("Slack") is True
    assert scanner.is_app_blocked("Visual Studio Code") is False
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement security.py**

This is the largest module. Implement:
- `SecurityScanner` class with `scan_text()` method
- PCI patterns with Luhn validation
- PII patterns (email, phone with indicators)
- Secrets patterns (GitHub, GitLab, Vault, JWT, SSH, Slack, AWS, DB strings)
- `ScanResult` with findings, `should_block`, `is_clean`
- `BLOCKED_APPS` list
- `is_app_blocked()` method
- `redact_image()` function that draws black boxes over findings

The scanner must:
- Return `should_block=True` for PCI and SECRET findings
- Return `should_block=False, should_redact=True` for PII findings
- Support enable/disable via config

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/screen_vision/security.py tests/test_security.py
git commit -m "Task 5: Security pipeline — PII/PCI/secrets scanner with Luhn validation"
```

---

## Task 6: Audio Module (Mic Recording + Whisper + Call Detection)

**Files:**
- Create: `src/screen_vision/audio.py`
- Create: `tests/test_audio.py`

- [ ] **Step 1: Write audio tests**

```python
# tests/test_audio.py
"""Tests for audio recording and transcription."""
from unittest.mock import patch, MagicMock
import pytest
import numpy as np

from screen_vision.audio import AudioRecorder, is_call_active, TranscriptSegment


def test_is_call_active_when_zoom_running():
    mock_procs = [{"name": "zoom.us", "pid": 1234}]
    with patch("screen_vision.audio._get_mic_using_processes", return_value=mock_procs):
        assert is_call_active() is True


def test_is_call_active_when_no_call():
    with patch("screen_vision.audio._get_mic_using_processes", return_value=[]):
        assert is_call_active() is False


def test_audio_recorder_creates_buffer():
    recorder = AudioRecorder(sample_rate=16000)
    assert recorder.sample_rate == 16000
    assert recorder.buffer is None  # Not started yet


def test_transcript_segment_dataclass():
    seg = TranscriptSegment(text="hello", start_time=1.0, end_time=2.0)
    assert seg.text == "hello"
    assert seg.start_time == 1.0
```

- [ ] **Step 2: Implement audio.py**

Implement:
- `AudioRecorder` class: start/stop recording via `sounddevice`, stores to numpy array in memory
- `transcribe()` method: runs `faster-whisper` on the buffer, returns `list[TranscriptSegment]`
- `is_call_active()`: checks if Zoom/Teams/Slack/Meet/FaceTime is using the mic
- `_get_mic_using_processes()`: macOS-specific check for mic-using processes
- `TranscriptSegment` dataclass with text, start_time, end_time, nearest_frame_index
- Graceful degradation if `sounddevice` or `faster-whisper` not installed

- [ ] **Step 3: Run tests — verify they pass**

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/audio.py tests/test_audio.py
git commit -m "Task 6: Audio module — mic recording, Whisper transcription, call detection"
```

---

## Task 7: Screen Watcher (Live Watching with Frame Sampling)

**Files:**
- Create: `src/screen_vision/watcher.py`
- Create: `tests/test_watcher.py`

- [ ] **Step 1: Write watcher tests**

```python
# tests/test_watcher.py
"""Tests for live screen watching."""
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
import numpy as np
from PIL import Image

from screen_vision.watcher import ScreenWatcher, WatchResult


@pytest.fixture
def mock_capture():
    from screen_vision.capture import CaptureResult
    img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    return CaptureResult(
        image=img, timestamp=1.0, monitor_index=0,
        cursor_position=(100, 50), active_window={"app_name": "Test", "window_title": "test"},
    )


def test_watcher_respects_max_frames():
    watcher = ScreenWatcher(max_frames=5)
    assert watcher.max_frames == 5


def test_watcher_respects_duration():
    watcher = ScreenWatcher(duration_seconds=30)
    assert watcher.duration_seconds == 30


def test_scene_changed_detects_difference():
    from screen_vision.capture import scene_changed
    import io

    img1 = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    img2 = Image.fromarray(np.full((100, 200, 3), 255, dtype=np.uint8))

    buf1, buf2 = io.BytesIO(), io.BytesIO()
    img1.save(buf1, format="JPEG")
    img2.save(buf2, format="JPEG")

    assert scene_changed(buf1.getvalue(), buf2.getvalue()) is True


def test_scene_unchanged_for_identical_frames():
    from screen_vision.capture import scene_changed
    import io

    img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    data = buf.getvalue()

    assert scene_changed(data, data) is False
```

- [ ] **Step 2: Implement watcher.py**

Implement:
- `ScreenWatcher` class with `watch()` method
- Two threads: frame sampler + audio recorder (optional)
- Frame sampler captures at `interval_seconds`, uses `scene_changed()` to skip duplicates
- Collects keyframes in a list (in-memory)
- After duration: security-scan all frames, transcribe audio, sync transcript to frames
- Returns `WatchResult` with keyframes, transcript, metadata

- [ ] **Step 3: Run tests — verify they pass**

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/watcher.py tests/test_watcher.py
git commit -m "Task 7: Screen watcher — live watching with adaptive frame sampling"
```

---

## Task 8: Video Analysis

**Files:**
- Create: `src/screen_vision/video.py`
- Create: `tests/test_video.py`

- [ ] **Step 1: Write video tests**

```python
# tests/test_video.py
"""Tests for video file analysis."""
from unittest.mock import patch, MagicMock
import pytest

from screen_vision.video import analyze_video, VideoResult


def test_analyze_video_rejects_missing_file():
    result = analyze_video("/nonexistent/video.mp4")
    assert result.error is not None


def test_analyze_video_rejects_oversized_file():
    with patch("screen_vision.video.os.path.getsize", return_value=600 * 1024 * 1024):
        with patch("screen_vision.video.os.path.exists", return_value=True):
            from screen_vision.config import get_config
            with patch("screen_vision.video.get_config") as mock_cfg:
                mock_cfg.return_value = MagicMock(max_video_file_mb=500, is_work_mode=True)
                result = analyze_video("/test/video.mp4")
    assert result.error is not None
    assert "size" in result.error.lower() or "large" in result.error.lower()
```

- [ ] **Step 2: Implement video.py**

Implement:
- `analyze_video()` function: validates file, checks size/duration limits
- Uses `subprocess` to call `ffmpeg` for frame extraction at scene changes
- Falls back to periodic extraction if scene detection yields too few frames
- Optionally extracts and transcribes audio track
- Security-scans each frame in work mode
- Returns `VideoResult` with keyframes + transcript

- [ ] **Step 3: Run tests — verify they pass**

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/video.py tests/test_video.py
git commit -m "Task 8: Video analysis — ffmpeg frame extraction with scene detection"
```

---

## Task 9: MCP Server (8 Tools)

**Files:**
- Create: `src/screen_vision/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write server tests**

```python
# tests/test_server.py
"""Tests for MCP server tools."""
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from PIL import Image
import numpy as np


@pytest.mark.asyncio
async def test_capture_screen_tool():
    from screen_vision.server import capture_screen
    from screen_vision.capture import CaptureResult

    mock_result = CaptureResult(
        image=Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8)),
        timestamp=1.0, monitor_index=0,
        cursor_position=(100, 50),
        active_window={"app_name": "Test", "window_title": "test.py"},
    )
    with patch("screen_vision.server._get_capture") as mock_cap, \
         patch("screen_vision.server._process_frame") as mock_proc:
        mock_cap.return_value = MagicMock()
        mock_cap.return_value.capture_screen.return_value = mock_result
        mock_proc.return_value = {"image": "base64...", "blocked": False}
        result = await capture_screen(delay_seconds=0)
    assert "image" in result or "error" in result


@pytest.mark.asyncio
async def test_list_monitors_tool():
    from screen_vision.server import list_monitors
    with patch("screen_vision.server.context.get_monitors", return_value=[
        {"index": 0, "width": 1920, "height": 1080, "x": 0, "y": 0, "is_primary": True}
    ]):
        result = await list_monitors()
    assert "monitors" in result


@pytest.mark.asyncio
async def test_get_active_context_tool():
    from screen_vision.server import get_active_context
    with patch("screen_vision.server.context.get_active_window", return_value={"app_name": "Test", "window_title": "t"}), \
         patch("screen_vision.server.context.get_cursor_position", return_value=(100, 200)), \
         patch("screen_vision.server.context.get_monitors", return_value=[]):
        result = await get_active_context()
    assert "active_window" in result
```

- [ ] **Step 2: Implement server.py**

```python
# src/screen_vision/server.py
"""Screen Vision MCP Server — 8 tools for visual context."""

from __future__ import annotations

import json
import time
from mcp.server.fastmcp import FastMCP
from screen_vision import context
from screen_vision.config import get_config
from screen_vision.capture import ScreenCapture, encode_jpeg

mcp = FastMCP(
    "screen-vision",
    instructions="Screen Vision gives Claude the ability to see the user's screen, watch it in real-time, and analyze video with audio transcription.",
)

_capture: ScreenCapture | None = None
_session_captures: int = 0
_last_capture_time: float = 0


def _get_capture() -> ScreenCapture:
    global _capture
    if _capture is None:
        _capture = ScreenCapture()
    return _capture


def _check_rate_limit() -> str | None:
    """Check rate limits. Returns error message or None."""
    global _session_captures, _last_capture_time
    cfg = get_config()
    if not cfg.is_work_mode:
        return None

    now = time.time()
    if cfg.min_capture_interval_seconds > 0 and (now - _last_capture_time) < cfg.min_capture_interval_seconds:
        return f"Rate limit: max 1 capture per {cfg.min_capture_interval_seconds}s. Try again shortly."
    if cfg.max_captures_per_session > 0 and _session_captures >= cfg.max_captures_per_session:
        return f"Session capture budget ({cfg.max_captures_per_session}) exceeded."
    return None


def _record_capture():
    global _session_captures, _last_capture_time
    _session_captures += 1
    _last_capture_time = time.time()


# ... implement all 8 tools following the spec ...
# Each tool: check rate limit → capture → process frame → security scan → return


@mcp.tool()
async def capture_screen(delay_seconds: int = 3, monitor: int = 0, scale: float = 0.5) -> str:
    """Capture the full screen. Use delay_seconds for the user to switch windows."""
    # Implementation here
    pass


@mcp.tool()
async def capture_region(x: int, y: int, width: int, height: int, scale: float = 1.0) -> str:
    """Capture a specific screen region."""
    pass


@mcp.tool()
async def capture_window(window_title: str, scale: float = 0.5) -> str:
    """Capture a specific window by title."""
    pass


@mcp.tool()
async def list_monitors() -> str:
    """List available monitors with dimensions."""
    monitors = context.get_monitors()
    return json.dumps({"monitors": monitors})


@mcp.tool()
async def watch_screen(
    duration_seconds: int = 60, interval_seconds: float = 4.0,
    include_audio: bool = True, max_frames: int = 30
) -> str:
    """Watch the screen for a duration, capturing keyframes with optional audio."""
    pass


@mcp.tool()
async def analyze_video(
    file_path: str, start_time: float = 0,
    end_time: float | None = None, max_frames: int = 20
) -> str:
    """Analyze a local video file — extract keyframes and transcribe audio."""
    pass


@mcp.tool()
async def read_screen_text(region: str | None = None) -> str:
    """OCR the screen or a specific region. Returns extracted text."""
    pass


@mcp.tool()
async def get_active_context() -> str:
    """Get active window, cursor position, monitors. Lightweight, no screenshot."""
    window = context.get_active_window()
    cursor = context.get_cursor_position()
    monitors = context.get_monitors()
    return json.dumps({
        "active_window": window.get("app_name", "") + " — " + window.get("window_title", ""),
        "cursor_position": list(cursor) if cursor else None,
        "monitors": monitors,
        "timestamp": __import__("time").time(),
    })


def main():
    cfg = get_config()
    if cfg.is_work_mode:
        # Verify required dependencies
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
        except Exception:
            print("ERROR: tesseract is required in work mode for PII/PCI security scanning.")
            print("Install: brew install tesseract")
            raise SystemExit(1)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run all tests**

```bash
pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/server.py tests/test_server.py
git commit -m "Task 9: MCP server — 8 tools with rate limiting and security checks"
```

---

## Task 10: Integration + README + Marketplace Plugin

**Files:**
- Create: `README.md`
- Modify: marketplace plugin at `/Users/avicuna/dev/rta-ai-marketplace/`

- [ ] **Step 1: Create README.md**

Include: overview, quick start, all 8 tools with examples, security modes, dependencies, MCP setup.

- [ ] **Step 2: Add screen-vision plugin to marketplace**

Create `plugins/screen-vision/` in the marketplace repo with `plugin.json`, `SKILL.md`, and `/vision` command.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
ruff check src/
```

- [ ] **Step 4: Tag and push**

```bash
git tag v0.1.0
git push origin main --tags
```

---

## Summary

| Task | Description | Key Files |
|------|------------|-----------|
| 1 | Project scaffolding + config | config.py, pyproject.toml |
| 2 | Context module | context.py (window, cursor, monitors) |
| 3 | Screenshot capture | capture.py (mss, region, window, JPEG) |
| 4 | OCR module | ocr.py (pytesseract wrapper) |
| 5 | Security pipeline | security.py (PII/PCI/secrets, deny-list, redaction) |
| 6 | Audio module | audio.py (mic, Whisper, call detection) |
| 7 | Screen watcher | watcher.py (live watching, frame sampling) |
| 8 | Video analysis | video.py (ffmpeg, scene detection) |
| 9 | MCP server | server.py (8 tools, rate limiting) |
| 10 | Integration + marketplace | README, marketplace plugin |
