"""Tests for phone camera MCP tools."""
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from screen_vision.server import (
    analyze_image,
    show_pairing_qr,
    capture_camera,
    watch_camera,
    phone_status,
)


class TestAnalyzeImageTool:
    """Tests for analyze_image MCP tool."""

    @pytest.mark.asyncio
    async def test_analyze_image_tool_success(self):
        """Should return JSON with analyzed image data."""
        with patch("screen_vision.server.analyze_mod") as mock_analyze_mod:
            mock_result = MagicMock()
            mock_result.base64_image = "base64data"
            mock_result.resolution = "1920x1080"
            mock_result.source = "file"
            mock_result.file_name = "test.jpg"
            mock_result.ocr_text = "Hello world"
            mock_result.security_redactions = 0
            mock_result.timestamp = "2026-03-19T10:00:00Z"
            mock_result.error = None
            mock_analyze_mod.analyze_image.return_value = mock_result

            result = await analyze_image("/path/to/test.jpg", "describe this")
            data = json.loads(result)

            assert data["image"] == "base64data"
            assert data["format"] == "jpeg"
            assert data["resolution"] == [1920, 1080]
            assert data["source"] == "file"
            assert data["file_name"] == "test.jpg"
            assert data["ocr_text"] == "Hello world"
            assert data["security_redactions"] == 0
            assert data["timestamp"] == "2026-03-19T10:00:00Z"

    @pytest.mark.asyncio
    async def test_analyze_image_tool_error(self):
        """Should return error JSON when analysis fails."""
        with patch("screen_vision.server.analyze_mod") as mock_analyze_mod:
            mock_result = MagicMock()
            mock_result.error = "File not found: /path/to/missing.jpg"
            mock_analyze_mod.analyze_image.return_value = mock_result

            result = await analyze_image("/path/to/missing.jpg")
            data = json.loads(result)

            assert data["error"] is True
            assert data["code"] == "ANALYSIS_FAILED"
            assert "File not found" in data["message"]

    @pytest.mark.asyncio
    async def test_analyze_image_tool_work_and_personal_modes(self):
        """Should work in both work and personal modes."""
        # Test personal mode
        with patch("screen_vision.server.analyze_mod") as mock_analyze_mod:
            mock_result = MagicMock()
            mock_result.base64_image = "base64data"
            mock_result.resolution = "1920x1080"
            mock_result.source = "file"
            mock_result.file_name = "test.jpg"
            mock_result.ocr_text = "Hello world"
            mock_result.security_redactions = 0
            mock_result.timestamp = "2026-03-19T10:00:00Z"
            mock_result.error = None
            mock_analyze_mod.analyze_image.return_value = mock_result

            result = await analyze_image("/path/to/test.jpg")
            data = json.loads(result)
            assert "error" not in data or not data.get("error")

        # Test work mode - should also work (analyze_image is available in both modes)
        with patch("screen_vision.server.analyze_mod") as mock_analyze_mod:
            mock_result = MagicMock()
            mock_result.base64_image = "base64data"
            mock_result.resolution = "1920x1080"
            mock_result.source = "file"
            mock_result.file_name = "work.jpg"
            mock_result.ocr_text = "Work data"
            mock_result.security_redactions = 2
            mock_result.timestamp = "2026-03-19T10:00:00Z"
            mock_result.error = None
            mock_analyze_mod.analyze_image.return_value = mock_result

            result = await analyze_image("/path/to/work.jpg")
            data = json.loads(result)
            assert data["security_redactions"] == 2


class TestShowPairingQRTool:
    """Tests for show_pairing_qr MCP tool."""

    @pytest.mark.asyncio
    async def test_show_pairing_qr_blocked_in_work_mode(self):
        """Should block QR generation in work mode."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True

            result = await show_pairing_qr()
            data = json.loads(result)

            assert data["error"] is True
            assert data["code"] == "WORK_MODE"
            assert "not available in work mode" in data["message"]

    @pytest.mark.asyncio
    async def test_show_pairing_qr_success_in_personal_mode(self):
        """Should generate QR code in personal mode."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.generate_pairing_qr.return_value = {
                    "url": "https://192.168.1.100:8443?token=abc123",
                    "qr_ascii": "█████\n█   █\n█████",
                    "expires_in_seconds": 60,
                    "instructions": "Scan this QR code with your iPhone camera."
                }
                mock_get_bridge.return_value = mock_bridge

                with patch("screen_vision.server._get_lan_ip") as mock_lan_ip:
                    mock_lan_ip.return_value = "192.168.1.100"

                    result = await show_pairing_qr()
                    data = json.loads(result)

                    assert "url" in data
                    assert "qr_ascii" in data
                    assert "expires_in_seconds" in data
                    assert data["expires_in_seconds"] == 60


class TestCaptureCameraTool:
    """Tests for capture_camera MCP tool."""

    @pytest.mark.asyncio
    async def test_capture_camera_blocked_in_work_mode(self):
        """Should block camera capture in work mode."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True

            result = await capture_camera()
            data = json.loads(result)

            assert data["error"] is True
            assert data["code"] == "WORK_MODE"

    @pytest.mark.asyncio
    async def test_capture_camera_no_phone_connected(self):
        """Should return error when no phone is connected."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = False
                mock_get_bridge.return_value = mock_bridge

                result = await capture_camera()
                data = json.loads(result)

                assert data["error"] is True
                assert data["code"] == "NO_PHONE"
                assert "No phone connected" in data["message"]

    @pytest.mark.asyncio
    async def test_capture_camera_no_frames_available(self):
        """Should return error when no frames have been received."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = True
                mock_bridge.frame_queue.get_latest.return_value = None
                mock_get_bridge.return_value = mock_bridge

                result = await capture_camera()
                data = json.loads(result)

                assert data["error"] is True
                assert data["code"] == "NO_FRAMES"

    @pytest.mark.asyncio
    async def test_capture_camera_success(self):
        """Should return frame data when phone is connected."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = True

                # Mock frame data (JPEG bytes and timestamp)
                frame_bytes = b"\xff\xd8\xff\xe0fake_jpeg_data"
                timestamp = time.time()
                mock_bridge.frame_queue.get_latest.return_value = (frame_bytes, timestamp)
                mock_get_bridge.return_value = mock_bridge

                result = await capture_camera()
                data = json.loads(result)

                assert "image" in data
                assert data["format"] == "jpeg"
                assert data["source"] == "phone_camera"
                assert "timestamp" in data
                assert "frame_age_ms" in data
                assert data["frame_age_ms"] >= 0


class TestWatchCameraTool:
    """Tests for watch_camera MCP tool."""

    @pytest.mark.asyncio
    async def test_watch_camera_blocked_in_work_mode(self):
        """Should block camera watching in work mode."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True

            result = await watch_camera(duration_seconds=10)
            data = json.loads(result)

            assert data["error"] is True
            assert data["code"] == "WORK_MODE"

    @pytest.mark.asyncio
    async def test_watch_camera_no_phone_connected(self):
        """Should return error when no phone is connected."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = False
                mock_get_bridge.return_value = mock_bridge

                result = await watch_camera(duration_seconds=5)
                data = json.loads(result)

                assert data["error"] is True
                assert data["code"] == "NO_PHONE"

    @pytest.mark.asyncio
    async def test_watch_camera_success(self):
        """Should collect frames over duration."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = True

                # Mock frame queue with some frames
                frame_bytes = b"\xff\xd8\xff\xe0fake_jpeg_data"
                timestamp = time.time()
                mock_bridge.frame_queue.get_latest.return_value = (frame_bytes, timestamp)
                mock_get_bridge.return_value = mock_bridge

                with patch("screen_vision.server.time.sleep"):  # Speed up test
                    result = await watch_camera(duration_seconds=1, include_audio=False, max_frames=5)
                    data = json.loads(result)

                    assert "keyframes" in data
                    assert "transcript" in data
                    assert "duration_actual" in data
                    assert "frames_captured" in data


class TestPhoneStatusTool:
    """Tests for phone_status MCP tool."""

    @pytest.mark.asyncio
    async def test_phone_status_in_work_mode(self):
        """Should return work mode status."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = True

            result = await phone_status()
            data = json.loads(result)

            assert data["connected"] is False
            assert data["mode"] == "work"
            assert "unavailable in work mode" in data["message"]

    @pytest.mark.asyncio
    async def test_phone_status_not_connected(self):
        """Should return disconnected status when no phone connected."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = False
                mock_bridge.is_running = False
                mock_bridge.frame_queue.__len__.return_value = 0
                mock_get_bridge.return_value = mock_bridge

                result = await phone_status()
                data = json.loads(result)

                assert data["connected"] is False
                assert data["frames_in_queue"] == 0
                assert data["server_running"] is False

    @pytest.mark.asyncio
    async def test_phone_status_connected(self):
        """Should return connected status when phone is connected."""
        with patch("screen_vision.server.get_config") as mock_config:
            mock_config.return_value.is_work_mode = False

            with patch("screen_vision.server._get_bridge") as mock_get_bridge:
                mock_bridge = MagicMock()
                mock_bridge.is_phone_connected = True
                mock_bridge.is_running = True
                mock_bridge.frame_queue.__len__.return_value = 15
                mock_get_bridge.return_value = mock_bridge

                result = await phone_status()
                data = json.loads(result)

                assert data["connected"] is True
                assert data["frames_in_queue"] == 15
                assert data["server_running"] is True
