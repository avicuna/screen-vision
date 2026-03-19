"""Tests for Audio module."""
import pytest
from unittest.mock import patch, MagicMock
import numpy as np
from screen_vision.audio import (
    TranscriptSegment,
    AudioRecorder,
    is_call_active,
    _get_mic_using_processes,
)


def test_transcript_segment_dataclass():
    """TranscriptSegment should have required fields."""
    segment = TranscriptSegment(
        text="Hello world",
        start_time=1.5,
        end_time=3.2,
        nearest_frame_index=42
    )

    assert segment.text == "Hello world"
    assert segment.start_time == 1.5
    assert segment.end_time == 3.2
    assert segment.nearest_frame_index == 42

    # Test with default nearest_frame_index
    segment2 = TranscriptSegment(text="Test", start_time=0.0, end_time=1.0)
    assert segment2.nearest_frame_index is None


def test_audio_recorder_creates_buffer():
    """AudioRecorder should initialize with empty buffer and correct state."""
    recorder = AudioRecorder(sample_rate=16000)

    assert recorder.sample_rate == 16000
    assert recorder.buffer is None
    assert recorder._recording is False


def test_is_call_active_when_zoom_running():
    """is_call_active should return True when Zoom is using mic."""
    mock_processes = [
        {"name": "zoom.us", "pid": 1234},
        {"name": "Chrome", "pid": 5678}
    ]

    with patch('screen_vision.audio._get_mic_using_processes', return_value=mock_processes):
        assert is_call_active() is True


def test_is_call_active_when_no_call():
    """is_call_active should return False when no call apps are using mic."""
    mock_processes = [
        {"name": "Spotify", "pid": 1234},
        {"name": "Terminal", "pid": 5678}
    ]

    with patch('screen_vision.audio._get_mic_using_processes', return_value=mock_processes):
        assert is_call_active() is False


def test_is_call_active_when_teams_running():
    """is_call_active should return True when Microsoft Teams is using mic."""
    mock_processes = [
        {"name": "Microsoft Teams", "pid": 9999}
    ]

    with patch('screen_vision.audio._get_mic_using_processes', return_value=mock_processes):
        assert is_call_active() is True


def test_is_call_active_when_empty():
    """is_call_active should return False when no processes are using mic."""
    with patch('screen_vision.audio._get_mic_using_processes', return_value=[]):
        assert is_call_active() is False


def test_transcribe_without_whisper():
    """AudioRecorder.transcribe should return empty list when whisper is not available."""
    recorder = AudioRecorder()
    recorder.buffer = np.random.randn(16000)  # 1 second of audio

    with patch('screen_vision.audio.WHISPER_AVAILABLE', False):
        segments = recorder.transcribe()

        assert segments == []
        assert isinstance(segments, list)


def test_clear_buffer():
    """AudioRecorder.clear should set buffer to None."""
    recorder = AudioRecorder()
    recorder.buffer = np.random.randn(16000)

    assert recorder.buffer is not None

    recorder.clear()

    assert recorder.buffer is None


def test_start_without_sounddevice():
    """AudioRecorder.start should raise clear error when sounddevice not installed."""
    recorder = AudioRecorder()

    with patch('screen_vision.audio.SOUNDDEVICE_AVAILABLE', False):
        with pytest.raises(RuntimeError, match="sounddevice not installed"):
            recorder.start(duration_seconds=1.0)


def test_start_with_sounddevice():
    """AudioRecorder.start should record audio when sounddevice is available."""
    recorder = AudioRecorder(sample_rate=16000)
    mock_audio = np.random.randn(16000, 1)  # 1 second mono

    with patch('screen_vision.audio.SOUNDDEVICE_AVAILABLE', True):
        with patch('screen_vision.audio.sd') as mock_sd:
            mock_sd.rec.return_value = mock_audio
            mock_sd.wait.return_value = None

            recorder.start(duration_seconds=1.0)

            mock_sd.rec.assert_called_once()
            call_kwargs = mock_sd.rec.call_args[1]
            assert call_kwargs['samplerate'] == 16000
            assert call_kwargs['channels'] == 1
            assert call_kwargs['dtype'] == 'float32'

            # Buffer should be stored (flattened)
            assert recorder.buffer is not None
            assert recorder.buffer.shape == (16000,)


def test_stop_when_not_recording():
    """AudioRecorder.stop should be safe to call when not recording."""
    recorder = AudioRecorder()
    recorder.stop()  # Should not raise

    assert recorder._recording is False


def test_transcribe_with_whisper():
    """AudioRecorder.transcribe should return segments when whisper is available."""
    recorder = AudioRecorder(sample_rate=16000)
    recorder.buffer = np.random.randn(16000)  # 1 second

    # Mock whisper model and segments
    mock_segment1 = MagicMock()
    mock_segment1.text = "Hello world"
    mock_segment1.start = 0.0
    mock_segment1.end = 1.5

    mock_segment2 = MagicMock()
    mock_segment2.text = "How are you"
    mock_segment2.start = 1.5
    mock_segment2.end = 3.0

    mock_segments = [mock_segment1, mock_segment2]
    mock_info = MagicMock()

    mock_model = MagicMock()
    mock_model.transcribe.return_value = (mock_segments, mock_info)

    with patch('screen_vision.audio.WHISPER_AVAILABLE', True):
        with patch('screen_vision.audio.WhisperModel', return_value=mock_model):
            segments = recorder.transcribe()

            assert len(segments) == 2
            assert isinstance(segments[0], TranscriptSegment)
            assert segments[0].text == "Hello world"
            assert segments[0].start_time == 0.0
            assert segments[0].end_time == 1.5
            assert segments[1].text == "How are you"
            assert segments[1].start_time == 1.5
            assert segments[1].end_time == 3.0


def test_get_mic_using_processes_macos():
    """_get_mic_using_processes should parse lsof output on macOS."""
    mock_output = """COMMAND    PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
zoom.us   1234 user   10u  CHR   14,2      0t0  123 /dev/cu.usbmodem
Teams     5678 user   15u  CHR   14,2      0t0  124 /dev/cu.usbmodem"""

    with patch('screen_vision.audio.subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout=mock_output,
            returncode=0
        )

        processes = _get_mic_using_processes()

        assert len(processes) >= 1
        # At minimum we should detect some processes from the output
        # The exact parsing depends on implementation


def test_is_call_active_handles_process_check_failure():
    """is_call_active should return False if process checking fails."""
    with patch('screen_vision.audio._get_mic_using_processes', side_effect=Exception("lsof failed")):
        assert is_call_active() is False
