# Phone Camera Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add phone camera support to Screen Vision MCP Server — file-drop analysis for work mode + live WebSocket camera stream for personal mode.

**Architecture:** Work mode gets `analyze_image()` tool (reads dropped files). Personal mode gets a background HTTPS+WebSocket camera bridge that accepts phone camera frames, plus `capture_camera()`, `watch_camera()`, `phone_status()`, and `show_pairing_qr()` tools.

**Tech Stack:** Python 3.11+, websockets (async WebSocket server), qrcode (QR generation), mkcert (local TLS), existing Screen Vision modules

**Spec:** `docs/superpowers/specs/2026-03-19-phone-camera-integration.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/screen_vision/analyze.py` | Image file analysis (AirDrop/file-drop) |
| `src/screen_vision/camera_bridge.py` | HTTPS + WebSocket server, frame queue, pairing, phone web app |
| `tests/test_analyze.py` | File-drop image analysis tests |
| `tests/test_camera_bridge.py` | Camera bridge, pairing, frame queue tests |

### Modified Files
| File | Changes |
|------|---------|
| `src/screen_vision/server.py` | Add 5 new MCP tools |
| `src/screen_vision/config.py` | Add camera bridge settings |
| `pyproject.toml` | Add websockets, qrcode dependencies |

---

## Task 1: Config Updates + analyze_image (Work Mode)

**Files:**
- Modify: `src/screen_vision/config.py`
- Create: `src/screen_vision/analyze.py`
- Create: `tests/test_analyze.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Add to the `dependencies` list:
```toml
"qrcode[pil]>=7.4.0",
"websockets>=12.0",
```

- [ ] **Step 2: Update config.py with camera bridge settings**

Add to the `Config` dataclass:
```python
# Camera bridge (personal mode only)
camera_bridge_port: int
camera_bridge_auto_shutdown_minutes: int
camera_bridge_max_frame_size_bytes: int
camera_bridge_max_fps: int
```

Work config: `camera_bridge_port=8443, auto_shutdown=10, max_frame_size=1048576, max_fps=10`
Personal config: same defaults.

- [ ] **Step 3: Write analyze tests**

```python
# tests/test_analyze.py
"""Tests for image file analysis."""
import os
import pytest
from PIL import Image
import numpy as np
from unittest.mock import patch

from screen_vision.analyze import analyze_image, AnalyzeResult


@pytest.fixture
def tmp_image(tmp_path):
    """Create a temp JPEG file."""
    img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    path = tmp_path / "test.jpg"
    img.save(str(path), format="JPEG")
    return str(path)


def test_analyze_image_success(tmp_image):
    result = analyze_image(tmp_image)
    assert result.error is None
    assert result.base64_image is not None
    assert len(result.base64_image) > 0
    assert result.source == "file"


def test_analyze_image_missing_file():
    result = analyze_image("/nonexistent/photo.jpg")
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_analyze_image_oversized(tmp_path):
    # Create a file that's "too large" by mocking os.path.getsize
    path = tmp_path / "big.jpg"
    img = Image.fromarray(np.zeros((10, 10, 3), dtype=np.uint8))
    img.save(str(path))
    with patch("screen_vision.analyze.os.path.getsize", return_value=60 * 1024 * 1024):
        result = analyze_image(str(path))
    assert result.error is not None
    assert "size" in result.error.lower() or "large" in result.error.lower()


def test_analyze_image_resizes_large_image(tmp_path):
    """Images larger than 2048px should be resized."""
    img = Image.fromarray(np.zeros((4000, 6000, 3), dtype=np.uint8))
    path = tmp_path / "huge.jpg"
    img.save(str(path), format="JPEG")
    result = analyze_image(str(path))
    assert result.error is None
    assert result.resolution[0] <= 2048 or result.resolution[1] <= 2048


def test_analyze_image_strips_exif(tmp_path):
    """EXIF should be stripped."""
    img = Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))
    path = tmp_path / "exif.jpg"
    img.save(str(path), format="JPEG")
    result = analyze_image(str(path))
    assert result.error is None
    # Verify by checking the base64 image doesn't contain EXIF markers
    # (Pillow strips by default when saving without exif param)
```

- [ ] **Step 4: Implement analyze.py**

```python
# src/screen_vision/analyze.py
"""Image file analysis — AirDrop/file-drop support."""

from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass

from PIL import Image

from screen_vision.config import get_config
from screen_vision.ocr import run_ocr, extract_text_near

MAX_FILE_SIZE_MB = 50
MAX_DIMENSION = 2048


@dataclass
class AnalyzeResult:
    base64_image: str | None
    resolution: tuple[int, int]
    source: str  # "file"
    file_name: str
    ocr_text: str
    timestamp: float
    security_redactions: int
    error: str | None = None


def analyze_image(file_path: str, prompt: str = "") -> AnalyzeResult:
    """Analyze a dropped image file."""
    # Validate file exists
    if not os.path.exists(file_path):
        return AnalyzeResult(
            base64_image=None, resolution=(0, 0), source="file",
            file_name="", ocr_text="", timestamp=time.time(),
            security_redactions=0, error=f"File not found: {file_path}",
        )

    # Check file size
    size_bytes = os.path.getsize(file_path)
    if size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        return AnalyzeResult(
            base64_image=None, resolution=(0, 0), source="file",
            file_name=os.path.basename(file_path), ocr_text="",
            timestamp=time.time(), security_redactions=0,
            error=f"File too large: {size_bytes // (1024*1024)}MB (max {MAX_FILE_SIZE_MB}MB)",
        )

    try:
        img = Image.open(file_path).convert("RGB")
    except Exception as e:
        return AnalyzeResult(
            base64_image=None, resolution=(0, 0), source="file",
            file_name=os.path.basename(file_path), ocr_text="",
            timestamp=time.time(), security_redactions=0,
            error=f"Cannot open image: {e}",
        )

    # Resize if too large
    if max(img.size) > MAX_DIMENSION:
        ratio = MAX_DIMENSION / max(img.size)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # OCR
    ocr_result = run_ocr(img)

    # Security scan (work mode)
    security_redactions = 0
    cfg = get_config()
    if cfg.is_work_mode and ocr_result.text:
        from screen_vision.security import SecurityScanner
        scanner = SecurityScanner(enabled=True)
        scan = scanner.scan_text(ocr_result.text)
        if scan.should_block:
            return AnalyzeResult(
                base64_image=None, resolution=img.size, source="file",
                file_name=os.path.basename(file_path), ocr_text="",
                timestamp=time.time(), security_redactions=0,
                error="Image blocked: sensitive data detected (PCI/secrets)",
            )
        security_redactions = len([f for f in scan.findings if f.action == "REDACT"])

    # Encode (strips EXIF by default)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return AnalyzeResult(
        base64_image=b64,
        resolution=img.size,
        source="file",
        file_name=os.path.basename(file_path),
        ocr_text=ocr_result.text[:500] if ocr_result.text else "",
        timestamp=time.time(),
        security_redactions=security_redactions,
    )
```

- [ ] **Step 5: Run tests — verify pass**

```bash
pip install -e ".[test]"
pytest tests/test_analyze.py -v
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Task 1: Add analyze_image for file-drop + config camera bridge settings"
```

---

## Task 2: Camera Bridge (WebSocket Server + Pairing)

**Files:**
- Create: `src/screen_vision/camera_bridge.py`
- Create: `tests/test_camera_bridge.py`

- [ ] **Step 1: Write camera bridge tests**

```python
# tests/test_camera_bridge.py
"""Tests for camera bridge — WebSocket server, pairing, frame queue."""
import pytest
import secrets
import time

from screen_vision.camera_bridge import (
    CameraBridge,
    PairingManager,
    FrameQueue,
    PHONE_APP_HTML,
)


class TestPairingManager:
    def test_generate_token(self):
        pm = PairingManager()
        token = pm.generate_token()
        assert len(token) == 64  # 32 bytes hex = 64 chars
        assert pm.pending_token is not None

    def test_validate_correct_token(self):
        pm = PairingManager()
        token = pm.generate_token()
        assert pm.validate_token(token) is True
        # Token should be consumed (one-time use)
        assert pm.pending_token is None

    def test_validate_wrong_token(self):
        pm = PairingManager()
        pm.generate_token()
        assert pm.validate_token("wrong-token") is False
        # Token should NOT be consumed on failure
        assert pm.pending_token is not None

    def test_token_expires(self):
        pm = PairingManager(expiry_seconds=1)
        pm.generate_token()
        time.sleep(1.1)
        assert pm.validate_token(pm.pending_token) is False

    def test_generate_pairing_url(self):
        pm = PairingManager()
        token = pm.generate_token()
        url = pm.get_pairing_url("192.168.1.100", 8443)
        assert "192.168.1.100" in url
        assert "8443" in url
        assert token in url


class TestFrameQueue:
    def test_push_and_get_latest(self):
        fq = FrameQueue(max_size=5)
        fq.push(b"frame1", time.time())
        fq.push(b"frame2", time.time())
        frame = fq.get_latest()
        assert frame is not None
        assert frame[0] == b"frame2"

    def test_max_size_eviction(self):
        fq = FrameQueue(max_size=3)
        for i in range(5):
            fq.push(f"frame{i}".encode(), time.time())
        assert len(fq) == 3

    def test_empty_queue_returns_none(self):
        fq = FrameQueue(max_size=5)
        assert fq.get_latest() is None

    def test_get_all(self):
        fq = FrameQueue(max_size=10)
        for i in range(5):
            fq.push(f"frame{i}".encode(), time.time())
        frames = fq.get_all()
        assert len(frames) == 5

    def test_clear(self):
        fq = FrameQueue(max_size=5)
        fq.push(b"frame", time.time())
        fq.clear()
        assert len(fq) == 0


class TestPhoneApp:
    def test_phone_html_exists(self):
        assert len(PHONE_APP_HTML) > 100
        assert "getUserMedia" in PHONE_APP_HTML
        assert "WebSocket" in PHONE_APP_HTML


class TestCameraBridge:
    def test_bridge_creation(self):
        bridge = CameraBridge(port=8443)
        assert bridge.port == 8443
        assert bridge.is_running is False
        assert bridge.is_phone_connected is False

    def test_bridge_pairing_generates_qr(self):
        bridge = CameraBridge(port=8443)
        qr_data = bridge.generate_pairing_qr("192.168.1.100")
        assert "url" in qr_data
        assert "qr_ascii" in qr_data
        assert "192.168.1.100" in qr_data["url"]
```

- [ ] **Step 2: Implement camera_bridge.py**

This is the largest new file. It contains:

1. **PairingManager** — generates one-time tokens, validates them, manages expiry
2. **FrameQueue** — thread-safe ring buffer for incoming camera frames
3. **PHONE_APP_HTML** — the HTML/JS web app served to the phone (getUserMedia + WebSocket)
4. **CameraBridge** — starts HTTPS+WSS server, handles connections, routes frames to queue

Key implementation details:
- Use `websockets` library for the WebSocket server
- Use `ssl` module for TLS (reads mkcert-generated cert files)
- Phone app HTML is a Python string constant (single-file, no external deps)
- Frame queue is thread-safe via `collections.deque` with maxlen
- Audio buffer separate from frame queue
- QR code generated via `qrcode` library, rendered as ASCII for terminal

The phone web app should:
- Request rear camera via `facingMode: "environment"`
- Canvas snapshot every 250ms → JPEG quality 70 → binary WebSocket message (prefix `0x01`)
- Optional mic: ScriptProcessor → PCM 16-bit 16kHz → binary message (prefix `0x02`)
- Show connection status, frame counter, start/pause button, mic toggle, camera flip

- [ ] **Step 3: Run tests — verify pass**

```bash
pytest tests/test_camera_bridge.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/camera_bridge.py tests/test_camera_bridge.py
git commit -m "Task 2: Camera bridge — WebSocket server, QR pairing, frame queue, phone web app"
```

---

## Task 3: New MCP Tools (5 tools)

**Files:**
- Modify: `src/screen_vision/server.py`
- Create: `tests/test_phone_tools.py`

- [ ] **Step 1: Write tool tests**

```python
# tests/test_phone_tools.py
"""Tests for phone camera MCP tools."""
import json
from unittest.mock import patch, MagicMock
import pytest

from screen_vision.analyze import AnalyzeResult


@pytest.mark.asyncio
async def test_analyze_image_tool():
    from screen_vision.server import analyze_image as tool
    mock_result = AnalyzeResult(
        base64_image="abc123", resolution=(200, 100), source="file",
        file_name="photo.jpg", ocr_text="hello", timestamp=1.0,
        security_redactions=0,
    )
    with patch("screen_vision.server.analyze_mod.analyze_image", return_value=mock_result):
        result = await tool("/tmp/photo.jpg")
    data = json.loads(result)
    assert data.get("source") == "file"
    assert "image" in data or "base64_image" in data


@pytest.mark.asyncio
async def test_analyze_image_error():
    from screen_vision.server import analyze_image as tool
    mock_result = AnalyzeResult(
        base64_image=None, resolution=(0, 0), source="file",
        file_name="", ocr_text="", timestamp=1.0,
        security_redactions=0, error="File not found",
    )
    with patch("screen_vision.server.analyze_mod.analyze_image", return_value=mock_result):
        result = await tool("/nonexistent.jpg")
    data = json.loads(result)
    assert data.get("error") is True


@pytest.mark.asyncio
async def test_phone_status_no_bridge():
    from screen_vision.server import phone_status
    result = await phone_status()
    data = json.loads(result)
    assert data["connected"] is False


@pytest.mark.asyncio
async def test_show_pairing_qr():
    from screen_vision.server import show_pairing_qr
    with patch("screen_vision.server._get_lan_ip", return_value="192.168.1.100"), \
         patch("screen_vision.server._get_bridge") as mock_bridge:
        bridge = MagicMock()
        bridge.generate_pairing_qr.return_value = {
            "url": "https://192.168.1.100:8443?token=abc",
            "qr_ascii": "█▀▀█",
            "expires_in_seconds": 60,
        }
        mock_bridge.return_value = bridge
        result = await show_pairing_qr()
    data = json.loads(result)
    assert "url" in data
    assert "qr_ascii" in data
```

- [ ] **Step 2: Add 5 tools to server.py**

Add to `src/screen_vision/server.py`:
- `from screen_vision import analyze as analyze_mod`
- `from screen_vision.camera_bridge import CameraBridge`

New tools:
```python
@mcp.tool()
async def analyze_image(file_path: str, prompt: str = "") -> str:
    """Analyze a dropped image file (AirDrop, screenshot, saved photo)."""

@mcp.tool()
async def show_pairing_qr() -> str:
    """Show QR code to connect phone camera. Personal mode only."""

@mcp.tool()
async def capture_camera(prompt: str = "") -> str:
    """Grab the latest frame from connected phone camera."""

@mcp.tool()
async def watch_camera(duration_seconds: int = 30, include_audio: bool = True, max_frames: int = 20) -> str:
    """Stream phone camera frames with optional audio."""

@mcp.tool()
async def phone_status() -> str:
    """Check phone camera connection status."""
```

Helper functions:
```python
_bridge: CameraBridge | None = None

def _get_bridge() -> CameraBridge:
    global _bridge
    if _bridge is None:
        cfg = get_config()
        _bridge = CameraBridge(port=cfg.camera_bridge_port)
    return _bridge

def _get_lan_ip() -> str:
    """Get the machine's LAN IP address."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
```

- [ ] **Step 3: Run all tests**

```bash
pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add src/screen_vision/server.py tests/test_phone_tools.py
git commit -m "Task 3: Add 5 phone camera MCP tools to server"
```

---

## Task 4: Integration Test + Push

**Files:**
- Modify: `README.md` — add phone camera section
- Push to GitLab

- [ ] **Step 1: Update README with phone camera docs**

Add sections:
- Phone Camera (Personal Mode) — how to set up mkcert, pair, use
- File-Drop Analysis (Work Mode) — AirDrop workflow
- New tools reference

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v
ruff check src/
```

- [ ] **Step 3: Commit and push**

```bash
git add -A
git commit -m "Phone camera integration v0.2.0: file-drop + WebSocket live stream"
git tag v0.2.0
git push origin main --tags
```

- [ ] **Step 4: Update marketplace**

Add phone camera tools to the screen-vision SKILL.md in `rta-ai-marketplace`.

---

## Summary

| Task | Description | New Files |
|------|------------|-----------|
| 1 | Config + analyze_image (work mode file-drop) | analyze.py, test_analyze.py |
| 2 | Camera bridge (WebSocket + pairing + phone app) | camera_bridge.py, test_camera_bridge.py |
| 3 | 5 new MCP tools in server.py | test_phone_tools.py |
| 4 | README + integration test + push | — |
