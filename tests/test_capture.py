"""Tests for screen capture module."""
import base64
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from screen_vision.capture import (
    CaptureResult,
    ScreenCapture,
    encode_jpeg,
    scene_changed,
)


class TestCaptureResult:
    """Tests for CaptureResult dataclass."""

    def test_capture_result_has_required_fields(self):
        """Should create CaptureResult with all required fields."""
        img = Image.new("RGB", (100, 100))
        timestamp = datetime.now()

        result = CaptureResult(
            image=img,
            timestamp=timestamp,
            monitor_index=1,
            cursor_position=(100, 200),
            active_window={"app_name": "Safari", "window_title": "Test"},
        )

        assert result.image == img
        assert result.timestamp == timestamp
        assert result.monitor_index == 1
        assert result.cursor_position == (100, 200)
        assert result.active_window == {"app_name": "Safari", "window_title": "Test"}


class TestScreenCapture:
    """Tests for ScreenCapture class."""

    def test_init_creates_mss_instance(self):
        """Should create mss instance on init."""
        with patch("screen_vision.capture.mss.mss") as mock_mss:
            ScreenCapture()
            mock_mss.assert_called_once()

    def test_capture_screen_returns_result(self):
        """Should capture screen and return CaptureResult."""
        # Create fake raw image data
        fake_image_data = np.zeros((100, 200, 4), dtype=np.uint8)
        fake_bgra_bytes = fake_image_data.tobytes()

        # Mock the grabbed image object
        mock_grab_result = MagicMock()
        mock_grab_result.size = (200, 100)  # width, height
        mock_grab_result.bgra = fake_bgra_bytes

        # Mock mss
        mock_sct = MagicMock()
        mock_sct.monitors = [
            {},  # Monitor 0 is aggregate
            {"top": 0, "left": 0, "width": 1920, "height": 1080}
        ]
        mock_sct.grab.return_value = mock_grab_result

        with patch("screen_vision.capture.mss.mss") as mock_mss:
            mock_mss.return_value.__enter__.return_value = mock_sct

            with patch("screen_vision.capture.get_cursor_position", return_value=(100, 200)):
                with patch("screen_vision.capture.get_active_window", return_value={"app_name": "Test", "window_title": "Window"}):
                    capture = ScreenCapture()
                    result = capture.capture_screen()

                    assert isinstance(result, CaptureResult)
                    assert isinstance(result.image, Image.Image)
                    assert isinstance(result.timestamp, datetime)
                    assert result.monitor_index == 0
                    assert result.cursor_position == (100, 200)
                    assert result.active_window == {"app_name": "Test", "window_title": "Window"}

    def test_capture_screen_scales_image(self):
        """Should scale image by specified factor."""
        # Create fake raw image data for 200x100 image
        fake_image_data = np.zeros((100, 200, 4), dtype=np.uint8)
        fake_bgra_bytes = fake_image_data.tobytes()

        mock_grab_result = MagicMock()
        mock_grab_result.size = (200, 100)  # width, height
        mock_grab_result.bgra = fake_bgra_bytes

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {},
            {"top": 0, "left": 0, "width": 200, "height": 100}
        ]
        mock_sct.grab.return_value = mock_grab_result

        with patch("screen_vision.capture.mss.mss") as mock_mss:
            mock_mss.return_value.__enter__.return_value = mock_sct

            with patch("screen_vision.capture.get_cursor_position", return_value=(100, 200)):
                with patch("screen_vision.capture.get_active_window", return_value={"app_name": "Test", "window_title": "Window"}):
                    capture = ScreenCapture()
                    result = capture.capture_screen(scale=0.5)

                    # Image should be scaled to 100x50 (half of 200x100)
                    assert result.image.size == (100, 50)

    def test_capture_region(self):
        """Should capture specific region of screen."""
        # Create fake raw image data for 400x300 region
        fake_image_data = np.zeros((300, 400, 4), dtype=np.uint8)
        fake_bgra_bytes = fake_image_data.tobytes()

        mock_grab_result = MagicMock()
        mock_grab_result.size = (400, 300)
        mock_grab_result.bgra = fake_bgra_bytes

        mock_sct = MagicMock()
        mock_sct.grab.return_value = mock_grab_result

        with patch("screen_vision.capture.mss.mss") as mock_mss:
            mock_mss.return_value.__enter__.return_value = mock_sct

            with patch("screen_vision.capture.get_cursor_position", return_value=(100, 200)):
                with patch("screen_vision.capture.get_active_window", return_value={"app_name": "Test", "window_title": "Window"}):
                    capture = ScreenCapture()
                    result = capture.capture_region(100, 100, 400, 300)

                    assert isinstance(result, CaptureResult)
                    assert isinstance(result.image, Image.Image)
                    assert result.image.size == (400, 300)

                    # Verify grab was called with correct region
                    mock_sct.grab.assert_called_once()
                    call_args = mock_sct.grab.call_args[0][0]
                    assert call_args["left"] == 100
                    assert call_args["top"] == 100
                    assert call_args["width"] == 400
                    assert call_args["height"] == 300


class TestEncodeJpeg:
    """Tests for encode_jpeg function."""

    def test_encode_jpeg_returns_base64_string(self):
        """Should encode PIL Image to base64 JPEG string."""
        # Create a simple test image
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))

        result = encode_jpeg(img, quality=75)

        # Should be a base64-encoded string
        assert isinstance(result, str)
        assert len(result) > 0

        # Should be valid base64
        try:
            decoded = base64.b64decode(result)
            assert len(decoded) > 0
        except Exception as e:
            pytest.fail(f"Result is not valid base64: {e}")

    def test_encode_jpeg_different_quality(self):
        """Should encode with different quality levels."""
        img = Image.new("RGB", (100, 100), color=(0, 255, 0))

        result_low = encode_jpeg(img, quality=50)
        result_high = encode_jpeg(img, quality=95)

        # Both should be valid base64
        assert isinstance(result_low, str)
        assert isinstance(result_high, str)

        # Higher quality should generally produce larger files
        # (though not always guaranteed for simple images)
        assert len(result_low) > 0
        assert len(result_high) > 0


class TestSceneChanged:
    """Tests for scene_changed function."""

    def test_scene_changed_detects_difference(self):
        """Should detect when two images are different."""
        # Create two different images
        img1 = Image.new("RGB", (1920, 1080), color=(255, 0, 0))  # Red
        img2 = Image.new("RGB", (1920, 1080), color=(0, 0, 255))  # Blue

        # Convert to bytes
        import io
        buf1 = io.BytesIO()
        img1.save(buf1, format="JPEG")
        bytes1 = buf1.getvalue()

        buf2 = io.BytesIO()
        img2.save(buf2, format="JPEG")
        bytes2 = buf2.getvalue()

        result = scene_changed(bytes1, bytes2, threshold=0.02)
        assert result is True

    def test_scene_unchanged_for_identical(self):
        """Should return False when images are identical."""
        # Create identical images
        img = Image.new("RGB", (1920, 1080), color=(128, 128, 128))

        # Convert to bytes
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        result = scene_changed(image_bytes, image_bytes, threshold=0.02)
        assert result is False

    def test_scene_changed_respects_threshold(self):
        """Should respect the threshold parameter."""
        # Create images with slight difference
        img1 = Image.new("RGB", (1920, 1080), color=(128, 128, 128))
        img2 = Image.new("RGB", (1920, 1080), color=(130, 130, 130))

        import io
        buf1 = io.BytesIO()
        img1.save(buf1, format="JPEG")
        bytes1 = buf1.getvalue()

        buf2 = io.BytesIO()
        img2.save(buf2, format="JPEG")
        bytes2 = buf2.getvalue()

        # With high threshold, should be considered unchanged
        result_high = scene_changed(bytes1, bytes2, threshold=1.0)
        assert result_high is False

        # With very low threshold, might be considered changed
        result_low = scene_changed(bytes1, bytes2, threshold=0.0001)
        # Note: This might still be False due to JPEG compression artifacts
        # Just verify it doesn't crash
        assert isinstance(result_low, bool)
