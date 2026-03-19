"""Screen watcher for real-time screen capture with scene change detection.

This module provides the ScreenWatcher class which captures frames at intervals,
detects scene changes, and optionally records audio with transcription.
"""
import io
import time
import threading
from dataclasses import dataclass
from PIL import Image

from screen_vision.capture import ScreenCapture, encode_jpeg, scene_changed
from screen_vision.audio import AudioRecorder, TranscriptSegment
from screen_vision.ocr import run_ocr, extract_text_near


@dataclass
class Keyframe:
    """A captured frame with metadata.

    Attributes:
        image: PIL Image object containing the captured frame
        base64_image: Base64-encoded JPEG string of the image
        timestamp: Time in seconds when frame was captured (relative to watch start)
        active_window: Dict with app_name and window_title of active window
        cursor_position: Tuple of (x, y) cursor coordinates, or None if unavailable
        ocr_near_cursor: Text extracted near the cursor position
        scene_changed: Whether this frame represents a scene change from previous
    """
    image: Image.Image
    base64_image: str
    timestamp: float
    active_window: dict
    cursor_position: tuple | None
    ocr_near_cursor: str
    scene_changed: bool


@dataclass
class WatchResult:
    """Result from a watch operation.

    Attributes:
        keyframes: List of captured Keyframe objects
        transcript: List of TranscriptSegment objects from audio recording
        duration_actual: Actual duration of the watch operation in seconds
        frames_captured: Total number of frames captured
        frames_skipped_duplicate: Number of frames skipped due to no scene change
        audio_recorded: Whether audio was successfully recorded
        security_redactions: Number of security redactions applied (Task 9)
        error: Error message if watch operation failed, None otherwise
    """
    keyframes: list[Keyframe]
    transcript: list  # TranscriptSegments from audio.py
    duration_actual: float
    frames_captured: int
    frames_skipped_duplicate: int
    audio_recorded: bool
    security_redactions: int
    error: str | None = None


class ScreenWatcher:
    """Watches the screen in real-time with scene change detection.

    Captures frames at regular intervals, detects scene changes to avoid
    duplicate frames, and optionally records audio with transcription.
    """

    def __init__(
        self,
        duration_seconds: int = 60,
        interval_seconds: float = 4.0,
        include_audio: bool = True,
        max_frames: int = 30
    ):
        """Initialize screen watcher.

        Args:
            duration_seconds: How long to watch the screen (default 60)
            interval_seconds: Time between frame captures (default 4.0)
            include_audio: Whether to record and transcribe audio (default True)
            max_frames: Maximum number of keyframes to keep (default 30)
        """
        self.duration_seconds = duration_seconds
        self.interval_seconds = interval_seconds
        self.include_audio = include_audio
        self.max_frames = max_frames

    def watch(self) -> WatchResult:
        """Watch the screen for the configured duration.

        This is a blocking call that runs for duration_seconds, capturing frames
        at interval_seconds intervals and optionally recording audio.

        Returns:
            WatchResult with captured keyframes, transcript, and metadata
        """
        # Initialize state
        keyframes: list[Keyframe] = []
        transcript: list[TranscriptSegment] = []
        frames_captured = 0
        frames_skipped = 0
        audio_recorded = False
        start_time = time.time()
        prev_jpeg_bytes: bytes | None = None

        # Initialize capture
        capture = ScreenCapture()

        # Start audio recording in background thread if enabled
        audio_thread = None
        audio_recorder = None
        if self.include_audio:
            try:
                audio_recorder = AudioRecorder()
                audio_thread = threading.Thread(
                    target=audio_recorder.start,
                    args=(self.duration_seconds,)
                )
                audio_thread.start()
            except Exception:
                # Audio recording failed - continue without it
                audio_recorder = None
                audio_thread = None

        # Frame capture loop
        try:
            while True:
                elapsed = time.time() - start_time

                # Check if duration exceeded
                if elapsed >= self.duration_seconds:
                    break

                # Capture frame
                result = capture.capture_screen(scale=0.5)
                frames_captured += 1

                # Convert to JPEG bytes for comparison
                jpeg_buffer = io.BytesIO()
                result.image.save(jpeg_buffer, format="JPEG", quality=75)
                curr_jpeg_bytes = jpeg_buffer.getvalue()

                # Check for scene change
                is_scene_changed = True
                if prev_jpeg_bytes is not None:
                    is_scene_changed = scene_changed(prev_jpeg_bytes, curr_jpeg_bytes, threshold=0.02)

                # Skip if no scene change
                if not is_scene_changed:
                    frames_skipped += 1
                    # Sleep until next interval
                    sleep_time = self.interval_seconds - (time.time() - start_time - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    continue

                # Update previous frame
                prev_jpeg_bytes = curr_jpeg_bytes

                # Encode to base64
                base64_image = encode_jpeg(result.image, quality=75)

                # Extract OCR near cursor
                ocr_near_cursor = ""
                if result.cursor_position is not None:
                    ocr_result = run_ocr(result.image)
                    cursor_dict = {
                        "x": result.cursor_position[0],
                        "y": result.cursor_position[1]
                    }
                    ocr_near_cursor = extract_text_near(
                        ocr_result.blocks,
                        cursor_dict,
                        radius=200
                    )

                # Create keyframe
                keyframe = Keyframe(
                    image=result.image,
                    base64_image=base64_image,
                    timestamp=elapsed,
                    active_window=result.active_window,
                    cursor_position=result.cursor_position,
                    ocr_near_cursor=ocr_near_cursor,
                    scene_changed=is_scene_changed,
                )

                # Add to keyframes (respecting max_frames limit)
                keyframes.append(keyframe)
                if len(keyframes) > self.max_frames:
                    # Remove oldest frame
                    keyframes.pop(0)

                # Sleep until next interval
                sleep_time = self.interval_seconds - (time.time() - start_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as e:
            # Capture any errors but continue to process what we have
            duration_actual = time.time() - start_time
            return WatchResult(
                keyframes=keyframes,
                transcript=transcript,
                duration_actual=duration_actual,
                frames_captured=frames_captured,
                frames_skipped_duplicate=frames_skipped,
                audio_recorded=audio_recorded,
                security_redactions=0,
                error=str(e),
            )

        # Wait for audio thread to complete
        if audio_thread is not None:
            audio_thread.join()

        # Transcribe audio if recorded
        if audio_recorder is not None and audio_recorder.buffer is not None:
            try:
                transcript = audio_recorder.transcribe()
                audio_recorded = True

                # Sync transcript to frames
                self._sync_transcript_to_frames(transcript, keyframes)
            except Exception:
                # Transcription failed - continue without it
                pass

        # Calculate actual duration
        duration_actual = time.time() - start_time

        return WatchResult(
            keyframes=keyframes,
            transcript=transcript,
            duration_actual=duration_actual,
            frames_captured=frames_captured,
            frames_skipped_duplicate=frames_skipped,
            audio_recorded=audio_recorded,
            security_redactions=0,  # Security scanning happens in Task 9
            error=None,
        )

    def _sync_transcript_to_frames(
        self,
        transcript: list[TranscriptSegment],
        keyframes: list[Keyframe]
    ) -> None:
        """Sync transcript segments to nearest keyframes.

        For each transcript segment, finds the keyframe with the closest
        timestamp and sets the nearest_frame_index field.

        Args:
            transcript: List of TranscriptSegment objects to sync
            keyframes: List of Keyframe objects to sync to

        Modifies transcript segments in-place.
        """
        if not transcript or not keyframes:
            return

        for segment in transcript:
            # Find nearest keyframe by timestamp
            min_distance = float('inf')
            nearest_index = 0

            for i, keyframe in enumerate(keyframes):
                # Use midpoint of segment for matching
                segment_midpoint = (segment.start_time + segment.end_time) / 2
                distance = abs(keyframe.timestamp - segment_midpoint)

                if distance < min_distance:
                    min_distance = distance
                    nearest_index = i

            # Set nearest frame index
            segment.nearest_frame_index = nearest_index
