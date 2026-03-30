"""macOS context module for getting active window, cursor position, and monitor info."""
import logging
import subprocess
import time
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


# Terminal apps that Claude Code typically runs in
TERMINAL_APPS = {"iTerm2", "Terminal", "Alacritty", "kitty", "Warp", "Hyper", "WezTerm", "ghostty", "Ghostty"}


logger = logging.getLogger("screen_vision")


def _is_app_visible(app: str) -> bool:
    """Check if a terminal app is currently visible via System Events."""
    try:
        # SECURITY: only called with values from TERMINAL_APPS whitelist
        result = _run_osascript(f'''
            tell application "System Events"
                return (visible of process "{app}") as text
            end tell
        ''')
        return result.strip() == "true"
    except Exception:
        return True  # assume visible on error (safer to wait longer)


def _wait_for_terminal_hidden(app: str, timeout: float = 2.0) -> bool:
    """Poll until the terminal is no longer visible on screen.

    Args:
        app: Terminal app name (must be in TERMINAL_APPS whitelist).
        timeout: Maximum seconds to wait before giving up.

    Returns:
        True if the terminal became hidden within the timeout, False otherwise.
    """
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if not _is_app_visible(app):
            # One extra compositor frame for the buffer to finish updating
            time.sleep(0.05)
            elapsed = time.monotonic() - start
            logger.debug("Terminal %s hidden after %.2fs", app, elapsed)
            return True
        time.sleep(0.1)
    return False


def hide_terminal() -> str | None:
    """Hide the frontmost terminal app. Returns the app name if hidden, None if not a terminal.

    Strategy:
    1. Send Cmd+H via System Events keystroke (triggers NSApplication hide:
       which properly invalidates the compositor).
    2. Verify the app is hidden by polling its visible attribute.
    3. If Cmd+H failed (non-Cocoa terminal, remapped shortcut), fall back
       to 'set visible to false'.

    NOTE: Cmd+H hides ALL windows of the terminal app, not just the
    frontmost one. This is inherent to macOS hide behavior.
    """
    try:
        active = get_active_window()
        app = active.get("app_name", "")
        if app not in TERMINAL_APPS:
            return None

        # SECURITY: `app` is safe to interpolate into AppleScript only because
        # it was validated against the hardcoded TERMINAL_APPS set above.
        # Do not move this interpolation outside that check.

        # Primary: Cmd+H via standard macOS hide path.
        # Poll for frontmost instead of fixed delay to eliminate the race.
        _run_osascript(f'''
            tell application "{app}" to activate
            tell application "System Events"
                repeat 20 times
                    if frontmost of process "{app}" then exit repeat
                    delay 0.05
                end repeat
                keystroke "h" using command down
            end tell
        ''')

        # Verify it worked; fall back to set-visible if Cmd+H was ignored
        # (non-Cocoa terminals like Kitty/Alacritty don't process it)
        time.sleep(0.3)
        if _is_app_visible(app):
            logger.warning("Cmd+H failed for %s, falling back to set visible", app)
            _run_osascript(f'''
                tell application "System Events"
                    set visible of process "{app}" to false
                end tell
            ''')

        return app
    except Exception:
        return None


def restore_terminal(app_name: str) -> None:
    """Restore a previously hidden terminal app and bring it to front."""
    try:
        _run_osascript(f'''
            tell application "{app_name}"
                activate
            end tell
        ''')
    except Exception:
        pass


def get_last_non_terminal_window() -> dict[str, str] | None:
    """Get the most recent non-terminal window. Used for smart capture targeting.

    When Claude Code is in the foreground, this finds what the user was
    ACTUALLY looking at before switching to the terminal.

    Returns:
        Dict with app_name and window_title, or None if not found.
    """
    try:
        # AppleScript to get the window list ordered by most recently focused
        script = """
        tell application "System Events"
            set procList to every application process whose visible is true
            set resultList to {}
            repeat with proc in procList
                try
                    set appName to name of proc
                    set winTitle to name of front window of proc
                    set end of resultList to appName & tab & winTitle
                end try
            end repeat
            return resultList as text
        end tell
        """
        result = _run_osascript(script)
        for line in result.split(", "):
            parts = line.split("\t", 1)
            if len(parts) == 2:
                app_name, title = parts
                if app_name not in TERMINAL_APPS:
                    return {"app_name": app_name, "window_title": title}
        return None
    except Exception:
        return None
