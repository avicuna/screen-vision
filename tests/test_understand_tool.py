"""Tests for understand_screen MCP tool."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from PIL import Image
import numpy as np

from screen_vision.understanding import UnderstandingResult


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
    return Image.fromarray(np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8))


@pytest.fixture
def mock_understanding_result():
    """Mock successful understanding result."""
    return UnderstandingResult(
        summary="VS Code showing booking.py with TypeError on line 42",
        application={"name": "VS Code", "type": "code_editor"},
        tags=["python", "error"],
        entities=[{"type": "error", "value": "TypeError", "details": {}}],
        actionable_insights=["Check type compatibility"],
        full_text="complete OCR text...",
        confidence=0.92,
        error=None,
        latency_ms=2340
    )


@pytest.mark.asyncio
async def test_understand_screen_returns_analysis(reset_rate_limit, sample_image, mock_understanding_result):
    """Mock capture + OCR + understanding, verify JSON structure."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = False
        mock_config.return_value.default_jpeg_quality = 75

        # Mock capture
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_result = MagicMock()
            mock_result.image = sample_image
            mock_result.cursor_position = (100, 100)
            mock_result.active_window = {"app_name": "VS Code", "window_title": "booking.py"}
            mock_capture.capture_screen.return_value = mock_result
            mock_get_capture.return_value = mock_capture

            # Mock OCR
            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr_result = MagicMock()
                mock_ocr_result.text = "complete OCR text..."
                mock_ocr.return_value = mock_ocr_result

                # Mock terminal hiding
                with patch("screen_vision.server.context.hide_terminal", return_value="iTerm2"):
                    with patch("screen_vision.server.context.restore_terminal"):
                        # Mock understanding
                        with patch("screen_vision.server.understand_image", new_callable=AsyncMock) as mock_understand:
                            mock_understand.return_value = mock_understanding_result

                            result = await understand_screen()

    # Parse JSON result
    data = json.loads(result)

    # Verify structure
    assert "understanding" in data
    assert "image" in data
    assert "format" in data
    assert "resolution" in data
    assert "full_text" in data
    assert "active_window" in data
    assert "timestamp" in data
    assert "security_redactions" in data
    assert "understanding_latency_ms" in data

    # Verify understanding content
    understanding = data["understanding"]
    assert understanding["summary"] == "VS Code showing booking.py with TypeError on line 42"
    assert understanding["application"]["name"] == "VS Code"
    assert understanding["application"]["type"] == "code_editor"
    assert "python" in understanding["tags"]
    assert "error" in understanding["tags"]
    assert len(understanding["entities"]) > 0
    assert understanding["entities"][0]["type"] == "error"
    assert len(understanding["actionable_insights"]) > 0
    assert understanding["confidence"] == 0.92

    # Verify image metadata
    assert data["format"] == "jpeg"
    assert data["resolution"] == [200, 100]  # width x height
    assert data["full_text"] == "complete OCR text..."
    assert "VS Code — booking.py" in data["active_window"]
    assert data["understanding_latency_ms"] == 2340


@pytest.mark.asyncio
async def test_understand_screen_with_prompt(reset_rate_limit, sample_image, mock_understanding_result):
    """Verify user prompt is passed through to understanding."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = False
        mock_config.return_value.default_jpeg_quality = 75

        # Mock capture
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_result = MagicMock()
            mock_result.image = sample_image
            mock_result.cursor_position = None
            mock_result.active_window = {}
            mock_capture.capture_screen.return_value = mock_result
            mock_get_capture.return_value = mock_capture

            # Mock OCR
            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr_result = MagicMock()
                mock_ocr_result.text = "some text"
                mock_ocr.return_value = mock_ocr_result

                # Mock terminal hiding
                with patch("screen_vision.server.context.hide_terminal", return_value=None):
                    with patch("screen_vision.server.context.restore_terminal"):
                        # Mock understanding
                        with patch("screen_vision.server.understand_image", new_callable=AsyncMock) as mock_understand:
                            mock_understand.return_value = mock_understanding_result

                            result = await understand_screen(prompt="what error is this?")

    # Verify prompt was passed to understand_image
    mock_understand.assert_called_once()
    call_kwargs = mock_understand.call_args.kwargs
    assert call_kwargs["prompt"] == "what error is this?"

    # Also verify the result is valid
    data = json.loads(result)
    assert "understanding" in data


@pytest.mark.asyncio
async def test_understand_screen_security_blocks(reset_rate_limit, sample_image):
    """In work mode, verify PCI data blocks the response.

    Note: This test uses the real SecurityScanner which will naturally detect
    the PCI data. The key is to verify the tool properly blocks sensitive data.
    """
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        # Use explicit values for all config attributes
        cfg = MagicMock()
        cfg.is_work_mode = True
        cfg.security_scanning_enabled = True
        cfg.default_jpeg_quality = 75
        # These need to be actual values for rate limiting
        cfg.min_capture_interval = 0.0
        cfg.max_captures_per_session = 100
        mock_config.return_value = cfg

        # Mock capture
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_result = MagicMock()
            mock_result.image = sample_image
            mock_result.cursor_position = None
            mock_result.active_window = {"app_name": "Test"}
            mock_capture.capture_screen.return_value = mock_result
            mock_get_capture.return_value = mock_capture

            # Mock OCR returning PCI data - real SecurityScanner will detect this
            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr_result = MagicMock()
                # Use a credit card number that will be detected
                mock_ocr_result.text = "Card number: 4532015112830366"
                mock_ocr.return_value = mock_ocr_result

                # Mock terminal hiding and async sleep
                with patch("screen_vision.server.context.hide_terminal", return_value=None):
                    with patch("screen_vision.server.context.restore_terminal"):
                        with patch("screen_vision.server.asyncio.sleep", new_callable=AsyncMock):
                            result = await understand_screen()

    # Verify security block - the real scanner should detect and block the PCI data
    data = json.loads(result)
    assert data.get("error") is True
    assert data.get("code") == "SECURITY_BLOCKED", f"Expected SECURITY_BLOCKED but got {data.get('code')}: {data.get('message', 'no message')}"
    assert "sensitive data" in data.get("message", "").lower()


@pytest.mark.asyncio
async def test_understand_screen_llm_failure_still_returns_ocr(reset_rate_limit, sample_image):
    """If Vision LLM fails, still return OCR text + image."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = False
        mock_config.return_value.default_jpeg_quality = 75

        # Mock capture
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_result = MagicMock()
            mock_result.image = sample_image
            mock_result.cursor_position = None
            mock_result.active_window = {}
            mock_capture.capture_screen.return_value = mock_result
            mock_get_capture.return_value = mock_capture

            # Mock OCR
            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr_result = MagicMock()
                mock_ocr_result.text = "preserved OCR text"
                mock_ocr.return_value = mock_ocr_result

                # Mock terminal hiding
                with patch("screen_vision.server.context.hide_terminal", return_value=None):
                    with patch("screen_vision.server.context.restore_terminal"):
                        # Mock understanding to fail
                        with patch("screen_vision.server.understand_image", new_callable=AsyncMock) as mock_understand:
                            failed_result = UnderstandingResult(
                                summary="",
                                application={"name": "unknown", "type": "other"},
                                tags=[],
                                entities=[],
                                actionable_insights=[],
                                full_text="preserved OCR text",
                                confidence=0.0,
                                error="LLM API call failed: Connection timeout",
                                latency_ms=5000
                            )
                            mock_understand.return_value = failed_result

                            result = await understand_screen()

    # Parse result
    data = json.loads(result)

    # Even though LLM failed, we should still get the image and OCR text
    assert "image" in data
    assert "full_text" in data
    assert data["full_text"] == "preserved OCR text"

    # Understanding should be present but degraded
    assert "understanding" in data
    understanding = data["understanding"]
    assert understanding["error"] is not None
    assert "timeout" in understanding["error"].lower()
    assert understanding["full_text"] == "preserved OCR text"


@pytest.mark.asyncio
async def test_understand_screen_respects_rate_limit(reset_rate_limit):
    """Should return rate limit error when limit is hit."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = True
        mock_config.return_value.min_capture_interval = 10.0
        mock_config.return_value.max_captures_per_session = 100
        mock_config.return_value.default_jpeg_quality = 75

        # First capture should succeed
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_result = MagicMock()
            mock_result.image = Image.new("RGB", (100, 100))
            mock_result.cursor_position = None
            mock_result.active_window = {}
            mock_capture.capture_screen.return_value = mock_result
            mock_get_capture.return_value = mock_capture

            with patch("screen_vision.server.run_ocr") as mock_ocr:
                mock_ocr_result = MagicMock()
                mock_ocr_result.text = ""
                mock_ocr.return_value = mock_ocr_result

                with patch("screen_vision.server.context.hide_terminal", return_value=None):
                    with patch("screen_vision.server.context.restore_terminal"):
                        with patch("screen_vision.server.understand_image", new_callable=AsyncMock) as mock_understand:
                            mock_understand.return_value = UnderstandingResult(
                                summary="test", application={}, tags=[], entities=[],
                                actionable_insights=[], full_text="", confidence=0.5
                            )

                            result1 = await understand_screen()
                            data1 = json.loads(result1)
                            assert "error" not in data1 or not data1.get("error")

        # Second capture immediately should fail
        result2 = await understand_screen()
        data2 = json.loads(result2)
        assert data2.get("error") is True
        assert data2.get("code") == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_understand_screen_exception_handling(reset_rate_limit):
    """Test graceful error handling on capture failure."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = False

        # Mock capture to raise an exception
        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_capture.capture_screen.side_effect = Exception("Screen capture failed")
            mock_get_capture.return_value = mock_capture

            with patch("screen_vision.server.context.hide_terminal", return_value=None):
                with patch("screen_vision.server.context.restore_terminal"):
                    result = await understand_screen()

    # Should return error JSON
    data = json.loads(result)
    assert data.get("error") is True
    assert data.get("code") == "INTERNAL_ERROR"
    assert "capture failed" in data.get("message", "").lower()


@pytest.mark.asyncio
async def test_understand_screen_terminal_restore_on_error(reset_rate_limit, sample_image):
    """Verify terminal is restored even if capture fails."""
    from screen_vision.server import understand_screen

    with patch("screen_vision.server.get_config") as mock_config:
        mock_config.return_value.is_work_mode = False

        with patch("screen_vision.server._get_capture") as mock_get_capture:
            mock_capture = MagicMock()
            mock_capture.capture_screen.side_effect = Exception("Capture error")
            mock_get_capture.return_value = mock_capture

            with patch("screen_vision.server.context.hide_terminal", return_value="iTerm2") as mock_hide:
                with patch("screen_vision.server.context.restore_terminal") as mock_restore:
                    result = await understand_screen()

                    # Terminal should be restored even on error
                    mock_hide.assert_called_once()
                    mock_restore.assert_called_once_with("iTerm2")

    data = json.loads(result)
    assert data.get("error") is True
