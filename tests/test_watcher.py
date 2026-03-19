"""Tests for screen watcher module."""
import io
from PIL import Image

from screen_vision.watcher import (
    Keyframe,
    WatchResult,
    ScreenWatcher,
)


class TestKeyframeDataclass:
    """Tests for Keyframe dataclass."""

    def test_keyframe_has_required_fields(self):
        """Should create Keyframe with all required fields."""
        img = Image.new("RGB", (100, 100))
        base64_img = "fake_base64_string"

        keyframe = Keyframe(
            image=img,
            base64_image=base64_img,
            timestamp=1.5,
            active_window={"app_name": "Safari", "window_title": "Test"},
            cursor_position=(100, 200),
            ocr_near_cursor="Hello World",
            scene_changed=True,
        )

        assert keyframe.image == img
        assert keyframe.base64_image == base64_img
        assert keyframe.timestamp == 1.5
        assert keyframe.active_window == {"app_name": "Safari", "window_title": "Test"}
        assert keyframe.cursor_position == (100, 200)
        assert keyframe.ocr_near_cursor == "Hello World"
        assert keyframe.scene_changed is True


class TestWatchResultDataclass:
    """Tests for WatchResult dataclass."""

    def test_watch_result_has_required_fields(self):
        """Should create WatchResult with all required fields."""
        keyframes = []
        transcript = []

        result = WatchResult(
            keyframes=keyframes,
            transcript=transcript,
            duration_actual=60.5,
            frames_captured=15,
            frames_skipped_duplicate=3,
            audio_recorded=True,
            security_redactions=2,
            error=None,
        )

        assert result.keyframes == keyframes
        assert result.transcript == transcript
        assert result.duration_actual == 60.5
        assert result.frames_captured == 15
        assert result.frames_skipped_duplicate == 3
        assert result.audio_recorded is True
        assert result.security_redactions == 2
        assert result.error is None

    def test_watch_result_with_error(self):
        """Should create WatchResult with error field."""
        result = WatchResult(
            keyframes=[],
            transcript=[],
            duration_actual=10.0,
            frames_captured=0,
            frames_skipped_duplicate=0,
            audio_recorded=False,
            security_redactions=0,
            error="Test error message",
        )

        assert result.error == "Test error message"


class TestScreenWatcherInit:
    """Tests for ScreenWatcher initialization."""

    def test_watcher_respects_max_frames(self):
        """Should store max_frames in initialization."""
        watcher = ScreenWatcher(max_frames=25)
        assert watcher.max_frames == 25

    def test_watcher_respects_duration(self):
        """Should store duration_seconds in initialization."""
        watcher = ScreenWatcher(duration_seconds=120)
        assert watcher.duration_seconds == 120

    def test_watcher_default_values(self):
        """Should use default values when not specified."""
        watcher = ScreenWatcher()
        assert watcher.duration_seconds == 60
        assert watcher.interval_seconds == 4.0
        assert watcher.include_audio is True
        assert watcher.max_frames == 30

    def test_watcher_custom_interval(self):
        """Should store custom interval_seconds."""
        watcher = ScreenWatcher(interval_seconds=2.0)
        assert watcher.interval_seconds == 2.0

    def test_watcher_audio_disabled(self):
        """Should store include_audio setting."""
        watcher = ScreenWatcher(include_audio=False)
        assert watcher.include_audio is False


class TestSceneChangeDetection:
    """Tests for scene change detection."""

    def test_scene_changed_detects_difference(self):
        """Should detect when two images are different."""
        from screen_vision.capture import scene_changed

        # Create two different images
        img1 = Image.new("RGB", (100, 100), color=(255, 0, 0))  # Red
        img2 = Image.new("RGB", (100, 100), color=(0, 0, 255))  # Blue

        # Convert to JPEG bytes
        buffer1 = io.BytesIO()
        img1.save(buffer1, format="JPEG")
        bytes1 = buffer1.getvalue()

        buffer2 = io.BytesIO()
        img2.save(buffer2, format="JPEG")
        bytes2 = buffer2.getvalue()

        # Should detect change
        assert scene_changed(bytes1, bytes2, threshold=0.02) is True

    def test_scene_unchanged_identical(self):
        """Should return False when images are identical."""
        from screen_vision.capture import scene_changed

        # Create identical image
        img = Image.new("RGB", (100, 100), color=(128, 128, 128))

        # Convert to JPEG bytes
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        jpeg_bytes = buffer.getvalue()

        # Should not detect change (same image)
        assert scene_changed(jpeg_bytes, jpeg_bytes, threshold=0.02) is False


class TestSyncTranscriptToFrames:
    """Tests for syncing transcript segments to keyframes."""

    def test_sync_transcript_to_frames(self):
        """Should assign nearest_frame_index to transcript segments."""
        from screen_vision.audio import TranscriptSegment

        # Create keyframes at t=0, 4, 8
        keyframes = [
            Keyframe(
                image=Image.new("RGB", (10, 10)),
                base64_image="img0",
                timestamp=0.0,
                active_window={},
                cursor_position=None,
                ocr_near_cursor="",
                scene_changed=True,
            ),
            Keyframe(
                image=Image.new("RGB", (10, 10)),
                base64_image="img1",
                timestamp=4.0,
                active_window={},
                cursor_position=None,
                ocr_near_cursor="",
                scene_changed=True,
            ),
            Keyframe(
                image=Image.new("RGB", (10, 10)),
                base64_image="img2",
                timestamp=8.0,
                active_window={},
                cursor_position=None,
                ocr_near_cursor="",
                scene_changed=True,
            ),
        ]

        # Create transcript at t=5 (should map to frame at t=4, index 1)
        transcript = [
            TranscriptSegment(
                text="Hello world",
                start_time=5.0,
                end_time=7.0,
            ),
        ]

        # Sync transcript to frames
        watcher = ScreenWatcher()
        watcher._sync_transcript_to_frames(transcript, keyframes)

        # Should assign to frame at index 1 (timestamp 4.0)
        assert transcript[0].nearest_frame_index == 1

    def test_sync_transcript_edge_cases(self):
        """Should handle edge cases in transcript syncing."""
        from screen_vision.audio import TranscriptSegment

        # Single keyframe at t=0
        keyframes = [
            Keyframe(
                image=Image.new("RGB", (10, 10)),
                base64_image="img0",
                timestamp=0.0,
                active_window={},
                cursor_position=None,
                ocr_near_cursor="",
                scene_changed=True,
            ),
        ]

        # Transcript at t=10 (should map to only available frame)
        transcript = [
            TranscriptSegment(
                text="Test",
                start_time=10.0,
                end_time=11.0,
            ),
        ]

        watcher = ScreenWatcher()
        watcher._sync_transcript_to_frames(transcript, keyframes)

        # Should assign to frame at index 0 (only frame available)
        assert transcript[0].nearest_frame_index == 0

    def test_sync_transcript_empty_lists(self):
        """Should handle empty transcript or keyframes gracefully."""
        watcher = ScreenWatcher()

        # Empty transcript
        watcher._sync_transcript_to_frames([], [])

        # Should not raise any errors
        assert True
