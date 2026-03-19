"""macOS context module for getting active window, cursor position, and monitor info."""
import subprocess
from typing import Any

import mss

# Try to import Quartz for native macOS APIs
try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
    )
    import Quartz
except ImportError:
    Quartz = None  # type: ignore
    CGWindowListCopyWindowInfo = None
    kCGWindowListOptionOnScreenOnly = None
    kCGNullWindowID = None

# Try to import NSEvent for cursor position
try:
    from Quartz import NSEvent
except ImportError:
    NSEvent = None  # type: ignore


def _run_osascript(script: str) -> str:
    """Run AppleScript and return stdout.

    Args:
        script: The AppleScript code to execute

    Returns:
        The stdout from osascript command

    Raises:
        CalledProcessError: If osascript returns non-zero exit code
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_cursor_position() -> tuple[int, int] | None:
    """Get cursor position using Quartz NSEvent (fallback to osascript).

    Returns:
        Tuple of (x, y) coordinates, or None if unable to get position.
        Coordinates are in top-left origin (standard screen coordinates).
    """
    # Try native Quartz first (fast and accurate)
    if NSEvent is not None:
        try:
            # Get mouse location in Cocoa coordinates (bottom-left origin)
            location = NSEvent.mouseLocation()
            x = int(location.x)
            y_bottom_left = int(location.y)

            # Convert to top-left origin by getting screen height
            with mss.mss() as sct:
                # Monitor 0 is aggregate, monitor 1 is primary
                if len(sct.monitors) > 1:
                    primary_monitor = sct.monitors[1]
                    screen_height = primary_monitor["height"]
                    y = screen_height - y_bottom_left
                    return (x, y)
        except Exception:
            pass  # Fall through to osascript fallback

    # Fallback to osascript
    try:
        script = """
        tell application "System Events"
            set {x, y} to (get position of mouse)
            return (x as string) & ", " & (y as string)
        end tell
        """
        result = _run_osascript(script)
        # Parse "x, y" format
        x_str, y_str = result.split(", ")
        return (int(x_str), int(y_str))
    except Exception:
        return None


def get_active_window() -> dict[str, str]:
    """Get frontmost application name and window title via osascript.

    Returns:
        Dict with app_name and window_title keys. Returns empty strings on error.
    """
    try:
        script = """
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            try
                set windowTitle to name of front window of frontApp
            on error
                set windowTitle to ""
            end try
            return appName & linefeed & windowTitle
        end tell
        """
        result = _run_osascript(script)
        lines = result.split("\n")
        app_name = lines[0] if len(lines) > 0 else ""
        window_title = lines[1] if len(lines) > 1 else ""

        return {
            "app_name": app_name,
            "window_title": window_title,
        }
    except Exception:
        return {
            "app_name": "",
            "window_title": "",
        }


def get_monitors() -> list[dict[str, Any]]:
    """Get monitor information via mss.

    Returns:
        List of monitor dicts with index, width, height, x, y, is_primary.
        Monitor 0 is always the aggregate of all monitors.
    """
    monitors = []
    with mss.mss() as sct:
        # Skip monitor 0 (aggregate), start from monitor 1
        for i, monitor in enumerate(sct.monitors[1:], start=1):
            monitors.append({
                "index": i,
                "width": monitor["width"],
                "height": monitor["height"],
                "x": monitor["left"],
                "y": monitor["top"],
                "is_primary": i == 1,  # First real monitor is primary
            })
    return monitors


def get_visible_windows() -> list[dict[str, Any]]:
    """Get all on-screen windows via Quartz CGWindowListCopyWindowInfo.

    Returns list of windows for deny-list checking. Falls back to empty list
    if Quartz not available.

    Returns:
        List of dicts with app_name, title, x, y, width, height.
        Returns empty list if Quartz unavailable or on error.
    """
    if Quartz is None or CGWindowListCopyWindowInfo is None:
        return []

    try:
        # Get all on-screen windows
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID
        )

        windows = []
        for window in window_list:
            # Extract window information
            app_name = window.get("kCGWindowOwnerName", "")
            title = window.get("kCGWindowName", "")
            bounds = window.get("kCGWindowBounds", {})

            windows.append({
                "app_name": app_name,
                "title": title,
                "x": int(bounds.get("X", 0)),
                "y": int(bounds.get("Y", 0)),
                "width": int(bounds.get("Width", 0)),
                "height": int(bounds.get("Height", 0)),
            })

        return windows
    except Exception:
        return []
