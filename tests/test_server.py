"""Tests for MCP server."""
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from PIL import Image

from screen_vision.server import (
    _check_rate_limit,
    _record_capture,
    _process_frame,
    _get_capture,
)


@pytest.fixture
def reset_rate_limit():
    """Reset rate limit globals before each test."""
    import screen_vision.server as server_module
    server_module._session_captures = 0
    server_module._last_capture_time = 0.0
    yield
    # Clean up after test
    server_module._session_captures = 0
    server_module._last_capture_time = 0.0


@pytest.fixture
def sample_image():
    """Create a simple test image."""
    import numpy as np
    return Image.fromarray(np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8))


class TestRateLimitCheck:
    """Tests for rate limit checking."""

    def test_rate_limit_allows_in_personal_mode(self, reset_rate_limit):
        """Should allow capture in personal mode regardless of rate."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False
            result = _check_rate_limit()
            assert result is None

    def test_rate_limit_blocks_when_interval_too_short(self, reset_rate_limit):
        """Should block capture if interval is too short in work mode."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True
            mock_config.return_value.min_capture_interval = 2.0
            mock_config.return_value.max_captures_per_session = 100

            # Record first capture
            _record_capture()

            # Try second capture immediately
            with patch("screen_vision.server.time.time") as mock_time:
                mock_time.return_value = 1.0  # Less than 2 seconds later
                result = _check_rate_limit()
                assert result is not None
                assert "Rate limit" in result

    def test_rate_limit_blocks_when_session_budget_exceeded(self, reset_rate_limit):
        """Should block capture if session budget is exceeded."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True
            mock_config.return_value.min_capture_interval = 0.0
            mock_config.return_value.max_captures_per_session = 2

            # Record captures up to limit
            _record_capture()
            _record_capture()

            # Try one more
            result = _check_rate_limit()
            assert result is not None
            assert "budget" in result.lower()

    def test_rate_limit_allows_when_interval_ok(self, reset_rate_limit):
        """Should allow capture if interval is sufficient."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True
            mock_config.return_value.min_capture_interval = 1.0
            mock_config.return_value.max_captures_per_session = 100

            # Record first capture at time 0
            with patch("screen_vision.server.time.time", return_value=0.0):
                _record_capture()

            # Check at time 2.0 (more than 1 second later)
            with patch("screen_vision.server.time.time", return_value=2.0):
                result = _check_rate_limit()
                assert result is None


class TestProcessFrame:
    """Tests for frame processing."""

    def test_process_frame_returns_basic_structure(self, sample_image):
        """Should return frame data with expected structure."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False
            mock_config.return_value.default_jpeg_quality = 75

            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr.return_value = MagicMock(text="", blocks=[])

                result = _process_frame(
                    sample_image,
                    cursor_pos=(100, 200),
                    active_window={"app_name": "Test", "window_title": "Window"}
                )

                assert "image" in result
                assert "format" in result
                assert result["format"] == "jpeg"
                assert "resolution" in result
                assert result["resolution"] == [200, 100]  # width x height
                assert result["cursor_position"] == [100, 200]
                assert "Test — Window" in result["active_window"]

    def test_process_frame_blocks_on_security_violation(self, sample_image):
        """Should block frame if security scanner detects sensitive data."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True

            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr.return_value = MagicMock(
                    text="Credit card: 4111111111111111",
                    blocks=[]
                )

                with patch("screen_vision.server.SecurityScanner") as mock_scanner_class:
                    mock_scanner = MagicMock()
                    mock_scanner_class.return_value = mock_scanner
                    mock_scanner.is_app_blocked.return_value = False

                    # Simulate security scan finding a BLOCK-level issue
                    mock_scan_result = MagicMock()
                    mock_scan_result.should_block = True
                    mock_scanner.scan_text.return_value = mock_scan_result

                    result = _process_frame(
                        sample_image,
                        cursor_pos=None,
                        active_window={"app_name": "Test"}
                    )

                    assert result.get("error") is True
                    assert result.get("code") == "SECURITY_BLOCKED"

    def test_process_frame_blocks_on_denied_app(self, sample_image):
        """Should block frame if app is in deny-list."""
        import screen_vision.server as srv
        srv._scanner = None  # Reset singleton so mock takes effect

        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True
            mock_config.return_value.security_scanning_enabled = True
            mock_config.return_value.default_jpeg_quality = 75

            with patch("screen_vision.server.SecurityScanner") as mock_scanner_class:
                mock_scanner = MagicMock()
                mock_scanner_class.return_value = mock_scanner
                mock_scanner.is_app_blocked.return_value = True

                result = _process_frame(
                    sample_image,
                    cursor_pos=None,
                    active_window={"app_name": "Slack", "window_title": ""}
                )

                assert result.get("error") is True
                assert result.get("code") == "APP_BLOCKED"


class TestCaptureScreenTool:
    """Tests for capture_screen MCP tool."""

    @pytest.mark.asyncio
    async def test_capture_screen_respects_rate_limit(self, reset_rate_limit):
        """Should return rate limit error when limit is hit."""
        # Import the tool function
        from screen_vision.server import capture_screen

        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True
            mock_config.return_value.min_capture_interval = 10.0
            mock_config.return_value.max_captures_per_session = 100

            # First capture should succeed
            with patch("screen_vision.server._get_capture") as mock_get_capture:
                mock_capture = MagicMock()
                mock_result = MagicMock()
                mock_result.image = Image.new("RGB", (100, 100))
                mock_result.cursor_position = None
                mock_result.active_window = {}
                mock_capture.capture_screen.return_value = mock_result
                mock_get_capture.return_value = mock_capture

                with patch("screen_vision.server._process_frame") as mock_process:
                    mock_process.return_value = {"image": "base64data"}

                    result1 = await capture_screen()
                    data1 = json.loads(result1)
                    assert "error" not in data1 or not data1.get("error")

            # Second capture immediately should fail
            result2 = await capture_screen()
            data2 = json.loads(result2)
            assert data2.get("error") is True
            assert data2.get("code") == "RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_capture_screen_returns_json(self, reset_rate_limit):
        """Should return valid JSON with image data."""
        from screen_vision.server import capture_screen

        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_capture") as mock_get_capture:
                mock_capture = MagicMock()
                mock_result = MagicMock()
                mock_result.image = Image.new("RGB", (100, 100))
                mock_result.cursor_position = (50, 50)
                mock_result.active_window = {"app_name": "Test", "window_title": "Window"}
                mock_capture.capture_screen.return_value = mock_result
                mock_get_capture.return_value = mock_capture

                with patch("screen_vision.server._process_frame") as mock_process:
                    mock_process.return_value = {
                        "image": "base64data",
                        "format": "jpeg",
                        "resolution": [100, 100],
                    }

                    result = await capture_screen()
                    data = json.loads(result)
                    assert "image" in data
                    assert data["format"] == "jpeg"


class TestListMonitorsTool:
    """Tests for list_monitors MCP tool."""

    @pytest.mark.asyncio
    async def test_list_monitors_returns_json(self):
        """Should return JSON with monitor information."""
        from screen_vision.server import list_monitors

        with patch("screen_vision.server.context.get_monitors") as mock_get_monitors:
            mock_get_monitors.return_value = [
                {"index": 1, "width": 1920, "height": 1080, "is_primary": True},
                {"index": 2, "width": 1680, "height": 1050, "is_primary": False},
            ]

            result = await list_monitors()
            data = json.loads(result)
            assert "monitors" in data
            assert len(data["monitors"]) == 2
            assert data["monitors"][0]["width"] == 1920


class TestGetActiveContextTool:
    """Tests for get_active_context MCP tool."""

    @pytest.mark.asyncio
    async def test_get_active_context_returns_json(self):
        """Should return JSON with context information."""
        from screen_vision.server import get_active_context

        with patch("screen_vision.server.context.get_cursor_position") as mock_cursor:
            with patch("screen_vision.server.context.get_active_window") as mock_window:
                with patch("screen_vision.server.context.get_monitors") as mock_monitors:
                    mock_cursor.return_value = (100, 200)
                    mock_window.return_value = {"app_name": "Safari", "window_title": "Test"}
                    mock_monitors.return_value = [{"index": 1, "width": 1920}]

                    result = await get_active_context()
                    data = json.loads(result)
                    assert "cursor_position" in data
                    assert data["cursor_position"] == [100, 200]
                    assert "active_window" in data
                    assert data["active_window"]["app_name"] == "Safari"
                    assert "monitors" in data
