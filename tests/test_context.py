"""Tests for macOS context module."""
import subprocess
from unittest.mock import Mock, patch, MagicMock

import pytest

from screen_vision.context import (
    _run_osascript,
    get_cursor_position,
    get_active_window,
    get_monitors,
    get_visible_windows,
)


class TestRunOsascript:
    """Tests for _run_osascript helper."""

    def test_returns_stdout(self):
        """Should return stdout from osascript command."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="test output",
                stderr="",
            )
            result = _run_osascript("tell application 'System Events'")
            assert result == "test output"
            mock_run.assert_called_once()

    def test_raises_on_error(self):
        """Should raise CalledProcessError on non-zero exit."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "osascript")
            with pytest.raises(subprocess.CalledProcessError):
                _run_osascript("invalid script")


class TestGetCursorPosition:
    """Tests for get_cursor_position."""

    def test_returns_tuple_from_quartz(self):
        """Should return (x, y) tuple from Quartz NSEvent."""
        with patch("screen_vision.context.NSEvent") as mock_nsevent:
            # Mock NSEvent.mouseLocation() to return an object with x, y attributes
            mock_location = Mock()
            mock_location.x = 423.0
            mock_location.y = 688.0  # Bottom-left coordinates
            mock_nsevent.mouseLocation.return_value = mock_location

            # Mock mss to get screen height
            with patch("screen_vision.context.mss.mss") as mock_mss:
                mock_sct = Mock()
                mock_sct.monitors = [
                    {},  # Monitor 0 is always the aggregate
                    {"top": 0, "left": 0, "width": 1920, "height": 1080}
                ]
                mock_mss.return_value.__enter__.return_value = mock_sct

                result = get_cursor_position()

                # Y should be flipped: 1080 - 688 = 392
                assert result == (423, 392)

    def test_fallback_to_osascript(self):
        """Should fall back to osascript when Quartz unavailable."""
        # Simulate ImportError for Quartz
        with patch("screen_vision.context.NSEvent", None):
            with patch("screen_vision.context._run_osascript") as mock_osascript:
                mock_osascript.return_value = "423, 312"
                result = get_cursor_position()
                assert result == (423, 312)

    def test_returns_none_on_error(self):
        """Should return None if cursor position cannot be determined."""
        with patch("screen_vision.context.NSEvent", None):
            with patch("screen_vision.context._run_osascript") as mock_osascript:
                mock_osascript.side_effect = Exception("osascript failed")
                result = get_cursor_position()
                assert result is None


class TestGetActiveWindow:
    """Tests for get_active_window."""

    def test_returns_dict_with_app_and_title(self):
        """Should return dict with app_name and window_title."""
        with patch("screen_vision.context._run_osascript") as mock_osascript:
            mock_osascript.return_value = "Safari\nScreen Vision - Documentation"
            result = get_active_window()
            assert result == {
                "app_name": "Safari",
                "window_title": "Screen Vision - Documentation",
            }

    def test_handles_missing_window_title(self):
        """Should handle case where window has no title."""
        with patch("screen_vision.context._run_osascript") as mock_osascript:
            mock_osascript.return_value = "Finder\n"
            result = get_active_window()
            assert result == {
                "app_name": "Finder",
                "window_title": "",
            }

    def test_returns_empty_on_error(self):
        """Should return empty strings on error."""
        with patch("screen_vision.context._run_osascript") as mock_osascript:
            mock_osascript.side_effect = Exception("osascript failed")
            result = get_active_window()
            assert result == {
                "app_name": "",
                "window_title": "",
            }


class TestGetMonitors:
    """Tests for get_monitors."""

    def test_returns_list_with_at_least_one_monitor(self):
        """Should return list with at least one monitor (real mss, no mock)."""
        monitors = get_monitors()

        assert isinstance(monitors, list)
        assert len(monitors) >= 1

        # Check first monitor has required fields
        monitor = monitors[0]
        assert "index" in monitor
        assert "width" in monitor
        assert "height" in monitor
        assert "x" in monitor
        assert "y" in monitor
        assert "is_primary" in monitor

        # Verify types
        assert isinstance(monitor["index"], int)
        assert isinstance(monitor["width"], int)
        assert isinstance(monitor["height"], int)
        assert isinstance(monitor["x"], int)
        assert isinstance(monitor["y"], int)
        assert isinstance(monitor["is_primary"], bool)

        # Verify reasonable values
        assert monitor["width"] > 0
        assert monitor["height"] > 0


class TestGetVisibleWindows:
    """Tests for get_visible_windows."""

    def test_returns_list_with_quartz(self):
        """Should return list of windows using Quartz."""
        # Mock CGWindowListCopyWindowInfo
        mock_windows = [
            {
                "kCGWindowOwnerName": "Safari",
                "kCGWindowName": "Screen Vision",
                "kCGWindowBounds": {"X": 100, "Y": 200, "Width": 800, "Height": 600},
            },
            {
                "kCGWindowOwnerName": "Terminal",
                "kCGWindowName": "bash",
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1024, "Height": 768},
            },
        ]

        with patch("screen_vision.context.CGWindowListCopyWindowInfo", return_value=mock_windows):
            with patch("screen_vision.context.Quartz", True):
                with patch("screen_vision.context.kCGWindowListOptionOnScreenOnly", 1):
                    with patch("screen_vision.context.kCGNullWindowID", 0):
                        result = get_visible_windows()

                        assert len(result) == 2
                        assert result[0] == {
                            "app_name": "Safari",
                            "title": "Screen Vision",
                            "x": 100,
                            "y": 200,
                            "width": 800,
                            "height": 600,
                        }
                        assert result[1] == {
                            "app_name": "Terminal",
                            "title": "bash",
                            "x": 0,
                            "y": 0,
                            "width": 1024,
                            "height": 768,
                        }

    def test_handles_missing_window_fields(self):
        """Should handle windows with missing optional fields."""
        mock_windows = [
            {
                "kCGWindowOwnerName": "Dock",
                # Missing kCGWindowName
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 100, "Height": 50},
            },
        ]

        with patch("screen_vision.context.CGWindowListCopyWindowInfo", return_value=mock_windows):
            with patch("screen_vision.context.Quartz", True):
                with patch("screen_vision.context.kCGWindowListOptionOnScreenOnly", 1):
                    with patch("screen_vision.context.kCGNullWindowID", 0):
                        result = get_visible_windows()

                        assert len(result) == 1
                        assert result[0]["title"] == ""

    def test_returns_empty_list_when_quartz_unavailable(self):
        """Should return empty list when Quartz is unavailable."""
        with patch("screen_vision.context.Quartz", None):
            result = get_visible_windows()
            assert result == []

    def test_returns_empty_list_on_error(self):
        """Should return empty list on error."""
        with patch("screen_vision.context.CGWindowListCopyWindowInfo", side_effect=Exception("Quartz error")):
            with patch("screen_vision.context.Quartz", True):
                result = get_visible_windows()
                assert result == []
