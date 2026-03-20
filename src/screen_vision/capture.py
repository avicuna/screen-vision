"""Screen capture module for taking screenshots and encoding images."""
import base64
import io
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime

import mss
import numpy as np
from PIL import Image

from screen_vision.context import get_cursor_position, get_active_window


@dataclass
class CaptureResult:
    """Result from a screen capture operation.

    Attributes:
        image: PIL Image object containing the captured screen
        timestamp: Timestamp when the capture was taken
        monitor_index: Index of the monitor that was captured (0 = all monitors)
        cursor_position: Tuple of (x, y) cursor coordinates, or None if unavailable
        active_window: Dict with app_name and window_title of active window
    """
    image: Image.Image
    timestamp: datetime
    monitor_index: int
    cursor_position: tuple[int, int] | None
    active_window: dict[str, str]


class ScreenCapture:
    """Class for capturing screenshots from the screen."""

    def __init__(self):
        """Initialize screen capture with mss instance."""
        self.sct = mss.mss()

    def capture_screen(
        self,
        delay_seconds: float = 0,
        monitor: int = 0,
        scale: float = 0.5
    ) -> CaptureResult:
        """Capture the entire screen or a specific monitor.

        Args:
            delay_seconds: Wait this many seconds before capturing
            monitor: Monitor index to capture (0 = all monitors, 1+ = specific monitor)
            scale: Scale factor for resizing the captured image (e.g., 0.5 = half size)

        Returns:
            CaptureResult with the captured image and metadata
        """
        # Auto-hide terminal so it doesn't occlude the screen
        from screen_vision.context import hide_terminal, restore_terminal
        hidden_app = hide_terminal()

        # macOS needs ~2s to fully update the screen buffer after hiding a window
        if hidden_app:
            time.sleep(2.0)
        elif delay_seconds > 0:
            # Only use manual delay if terminal wasn't auto-hidden
            time.sleep(delay_seconds)

        # Capture the screen (with terminal restore in finally)
        try:
            with mss.mss() as sct:
                if monitor == 0:
                    monitor_dict = sct.monitors[0]
                else:
                    monitor_dict = sct.monitors[monitor]

                grabbed = sct.grab(monitor_dict)
                img = Image.frombytes("RGB", grabbed.size, grabbed.bgra, "raw", "BGRX")

                if scale != 1.0:
                    new_width = int(img.width * scale)
                    new_height = int(img.height * scale)
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            cursor_pos = get_cursor_position()
            active_win = get_active_window()
            timestamp = datetime.now()

            return CaptureResult(
                image=img,
                timestamp=timestamp,
                monitor_index=monitor,
                cursor_position=cursor_pos,
                active_window=active_win,
            )
        finally:
            # Always restore the terminal, even if capture fails
            if hidden_app:
                restore_terminal(hidden_app)

    def capture_region(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        scale: float = 1.0
    ) -> CaptureResult:
        """Capture a specific region of the screen.

        Args:
            x: Left coordinate of the region
            y: Top coordinate of the region
            width: Width of the region
            height: Height of the region
            scale: Scale factor for resizing the captured image

        Returns:
            CaptureResult with the captured region and metadata
        """
        # Define the region to capture
        region = {
            "left": x,
            "top": y,
            "width": width,
            "height": height,
        }

        # Grab the region
        with mss.mss() as sct:
            grabbed = sct.grab(region)

            # Convert BGRA to RGB
            img = Image.frombytes("RGB", grabbed.size, grabbed.bgra, "raw", "BGRX")

            # Scale the image if needed
            if scale != 1.0:
                new_width = int(img.width * scale)
                new_height = int(img.height * scale)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Get context information
        cursor_pos = get_cursor_position()
        active_win = get_active_window()
        timestamp = datetime.now()

        return CaptureResult(
            image=img,
            timestamp=timestamp,
            monitor_index=-1,  # -1 indicates a custom region
            cursor_position=cursor_pos,
            active_window=active_win,
        )

    def capture_window(
        self,
        window_title: str,
        scale: float = 0.5
    ) -> CaptureResult:
        """Capture a specific window by title using macOS screencapture.

        Uses macOS native screencapture command for clean window capture.
        Falls back to full screen capture if window cannot be found.

        Args:
            window_title: Title of the window to capture
            scale: Scale factor for resizing the captured image

        Returns:
            CaptureResult with the captured window and metadata
        """
        # Try to get window ID using AppleScript
        try:
            # Sanitize window_title to prevent AppleScript injection
            safe_title = window_title.replace('\\', '\\\\').replace('"', '\\"')

            # Use AppleScript to find the window ID
            script = f"""
            tell application "System Events"
                set windowList to every window of (first application process whose frontmost is true)
                repeat with w in windowList
                    if name of w is "{safe_title}" then
                        return id of w
                    end if
                end repeat
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=True,
            )
            window_id = result.stdout.strip()

            if window_id:
                # Use screencapture with window ID
                temp_file = f"/tmp/screencapture_{window_id}.png"
                subprocess.run(
                    ["screencapture", "-l", window_id, temp_file],
                    check=True,
                )

                # Load the image
                img = Image.open(temp_file)

                # Scale if needed
                if scale != 1.0:
                    new_width = int(img.width * scale)
                    new_height = int(img.height * scale)
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Clean up temp file
                subprocess.run(["rm", temp_file], check=False)

                # Get context information
                cursor_pos = get_cursor_position()
                active_win = get_active_window()
                timestamp = datetime.now()

                return CaptureResult(
                    image=img,
                    timestamp=timestamp,
                    monitor_index=-2,  # -2 indicates window capture
                    cursor_position=cursor_pos,
                    active_window=active_win,
                )
        except Exception:
            pass  # Fall through to full screen capture

        # Fallback: capture full screen
        return self.capture_screen(scale=scale)


def encode_jpeg(image: Image.Image, quality: int = 75) -> str:
    """Encode a PIL Image as a base64 JPEG string.

    Args:
        image: PIL Image object to encode
        quality: JPEG quality level (1-95, default 75)

    Returns:
        Base64-encoded JPEG string
    """
    # Create a bytes buffer
    buffer = io.BytesIO()

    # Save as JPEG to the buffer (EXIF data is not included by default)
    image.save(buffer, format="JPEG", quality=quality)

    # Get the bytes and encode as base64
    jpeg_bytes = buffer.getvalue()
    base64_string = base64.b64encode(jpeg_bytes).decode("utf-8")

    return base64_string


def scene_changed(
    prev_bytes: bytes,
    curr_bytes: bytes,
    threshold: float = 0.02
) -> bool:
    """Compare two images to detect if the scene has changed.

    Uses downscaled thumbnails for fast comparison.

    Args:
        prev_bytes: Bytes of the previous image (JPEG)
        curr_bytes: Bytes of the current image (JPEG)
        threshold: Difference threshold (0.0-1.0). Higher = less sensitive.

    Returns:
        True if the scene has changed, False otherwise
    """
    try:
        # Load images from bytes
        prev_img = Image.open(io.BytesIO(prev_bytes))
        curr_img = Image.open(io.BytesIO(curr_bytes))

        # Create small thumbnails for comparison (160x90)
        thumbnail_size = (160, 90)
        prev_thumb = prev_img.resize(thumbnail_size, Image.Resampling.LANCZOS)
        curr_thumb = curr_img.resize(thumbnail_size, Image.Resampling.LANCZOS)

        # Convert to numpy arrays
        prev_array = np.array(prev_thumb, dtype=np.float32)
        curr_array = np.array(curr_thumb, dtype=np.float32)

        # Calculate pixel difference
        diff = np.abs(prev_array - curr_array)

        # Calculate mean difference normalized to 0-1 range
        mean_diff = np.mean(diff) / 255.0

        # Return True if difference exceeds threshold
        return bool(mean_diff > threshold)

    except Exception:
        # If comparison fails, assume scene changed
        return True
