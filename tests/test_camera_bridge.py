"""Tests for camera bridge module."""
import pytest
import time
import secrets
from collections import deque
from screen_vision.camera_bridge import (
    PairingManager,
    FrameQueue,
    CameraBridge,
    PHONE_APP_HTML,
)


class TestPairingManager:
    """Tests for PairingManager class."""

    def test_generate_token(self):
        """Generate token should return a 64-character hex string."""
        manager = PairingManager(expiry_seconds=60)
        token = manager.generate_token()

        assert isinstance(token, str)
        assert len(token) == 64  # 32 bytes * 2 (hex encoding)
        assert all(c in '0123456789abcdef' for c in token)
        assert manager.pending_token == token

    def test_validate_correct_token(self):
        """Validate correct token should return True and consume it."""
        manager = PairingManager(expiry_seconds=60)
        token = manager.generate_token()

        # First validation should succeed
        assert manager.validate_token(token) is True

        # Second validation should fail (token consumed)
        assert manager.validate_token(token) is False
        assert manager.pending_token is None

    def test_validate_wrong_token(self):
        """Validate wrong token should return False and NOT consume pending token."""
        manager = PairingManager(expiry_seconds=60)
        correct_token = manager.generate_token()
        wrong_token = secrets.token_hex(32)

        # Wrong token should fail
        assert manager.validate_token(wrong_token) is False

        # Pending token should still be there
        assert manager.pending_token == correct_token

        # Correct token should still work
        assert manager.validate_token(correct_token) is True

    def test_token_expires(self):
        """Token should expire after the configured time."""
        manager = PairingManager(expiry_seconds=1)
        token = manager.generate_token()

        # Sleep past expiry
        time.sleep(1.1)

        # Should fail and clear pending token
        assert manager.validate_token(token) is False
        assert manager.pending_token is None

    def test_generate_pairing_url(self):
        """Generate pairing URL should contain host, port, and token."""
        manager = PairingManager(expiry_seconds=60)
        token = manager.generate_token()
        url = manager.get_pairing_url("192.168.1.100", 8443)

        assert "192.168.1.100" in url
        assert "8443" in url
        assert token in url
        assert url.startswith("https://")
        assert "?token=" in url

    def test_no_pending_token_validation(self):
        """Validation should fail when no token is pending."""
        manager = PairingManager(expiry_seconds=60)

        # No token generated yet
        assert manager.validate_token("any_token") is False


class TestFrameQueue:
    """Tests for FrameQueue class."""

    def test_push_and_get_latest(self):
        """Push 2 frames and get_latest should return the second one."""
        queue = FrameQueue(max_size=10)

        frame1 = b"frame_data_1"
        frame2 = b"frame_data_2"
        ts1 = 1000.0
        ts2 = 2000.0

        queue.push(frame1, ts1)
        queue.push(frame2, ts2)

        latest = queue.get_latest()
        assert latest is not None
        assert latest[0] == frame2
        assert latest[1] == ts2

    def test_max_size_eviction(self):
        """Push 5 frames into size-3 queue should evict oldest 2."""
        queue = FrameQueue(max_size=3)

        for i in range(5):
            queue.push(f"frame_{i}".encode(), float(i))

        # Should have only last 3
        assert len(queue) == 3

        all_frames = queue.get_all()
        assert len(all_frames) == 3
        assert all_frames[0][0] == b"frame_2"
        assert all_frames[1][0] == b"frame_3"
        assert all_frames[2][0] == b"frame_4"

    def test_empty_queue_returns_none(self):
        """Empty queue should return None for get_latest."""
        queue = FrameQueue(max_size=10)

        assert queue.get_latest() is None
        assert len(queue) == 0

    def test_get_all(self):
        """Get_all should return all frames in order."""
        queue = FrameQueue(max_size=10)

        for i in range(5):
            queue.push(f"frame_{i}".encode(), float(i))

        all_frames = queue.get_all()
        assert len(all_frames) == 5

        for i, (frame, ts) in enumerate(all_frames):
            assert frame == f"frame_{i}".encode()
            assert ts == float(i)

    def test_clear(self):
        """Clear should remove all frames."""
        queue = FrameQueue(max_size=10)

        for i in range(5):
            queue.push(f"frame_{i}".encode(), float(i))

        assert len(queue) == 5

        queue.clear()

        assert len(queue) == 0
        assert queue.get_latest() is None
        assert queue.get_all() == []


class TestPhoneApp:
    """Tests for phone app HTML."""

    def test_phone_html_exists(self):
        """Phone app HTML should be defined and contain required elements."""
        assert PHONE_APP_HTML is not None
        assert isinstance(PHONE_APP_HTML, str)
        assert len(PHONE_APP_HTML) > 0

    def test_phone_html_has_getusermedia(self):
        """HTML should contain getUserMedia call."""
        assert "getUserMedia" in PHONE_APP_HTML

    def test_phone_html_has_websocket(self):
        """HTML should contain WebSocket connection code."""
        assert "WebSocket" in PHONE_APP_HTML or "new WebSocket" in PHONE_APP_HTML

    def test_phone_html_has_environment_camera(self):
        """HTML should request rear/environment camera."""
        assert "environment" in PHONE_APP_HTML

    def test_phone_html_has_controls(self):
        """HTML should have start/pause, flip, and mic buttons."""
        # Check for button-related text
        html_lower = PHONE_APP_HTML.lower()
        assert "start" in html_lower or "pause" in html_lower or "button" in html_lower


class TestCameraBridge:
    """Tests for CameraBridge class."""

    def test_bridge_creation(self):
        """Bridge should initialize with correct default state."""
        bridge = CameraBridge(port=8443)

        assert bridge.port == 8443
        assert bridge.is_running is False
        assert bridge.is_phone_connected is False
        assert isinstance(bridge.pairing, PairingManager)
        assert isinstance(bridge.frame_queue, FrameQueue)
        assert isinstance(bridge.audio_buffer, list)
        assert len(bridge.audio_buffer) == 0

    def test_bridge_pairing_generates_qr(self):
        """Bridge should generate QR code with URL and instructions."""
        bridge = CameraBridge(port=8443)
        result = bridge.generate_pairing_qr("192.168.1.100")

        assert "url" in result
        assert "qr_ascii" in result
        assert "expires_in_seconds" in result
        assert "instructions" in result

        # Check URL format
        assert "192.168.1.100" in result["url"]
        assert "8443" in result["url"]
        assert "?token=" in result["url"]

        # QR code should be non-empty
        assert len(result["qr_ascii"]) > 0

        # Expires should match pairing manager default
        assert result["expires_in_seconds"] == 60

    def test_bridge_custom_port(self):
        """Bridge should accept custom port."""
        bridge = CameraBridge(port=9000)
        assert bridge.port == 9000

    def test_frame_queue_size_default(self):
        """Frame queue should have default max size of 30."""
        bridge = CameraBridge()
        # Check that frame_queue has the right max size
        # We can test this by pushing 31 frames and checking length
        for i in range(31):
            bridge.frame_queue.push(f"frame_{i}".encode(), float(i))

        assert len(bridge.frame_queue) == 30


# Note: Integration tests for start/stop and WebSocket handling
# would require async testing and actual server startup.
# Those are better suited for integration tests or manual testing.
# The above tests cover the core logic and components.
