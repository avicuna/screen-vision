"""Tests for Video module."""
import pytest
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path
from PIL import Image
import numpy as np
from screen_vision.video import (
    VideoResult,
    analyze_video,
)


def test_video_result_dataclass():
    """VideoResult should have required fields."""
    result = VideoResult(
        keyframes=[
            {"image": Image.new("RGB", (100, 100)), "timestamp": 1.5},
            {"image": Image.new("RGB", (100, 100)), "timestamp": 3.0},
        ],
        transcript=[],
        duration=10.5,
        frames_extracted=2,
        error=None
    )

    assert len(result.keyframes) == 2
    assert result.keyframes[0]["timestamp"] == 1.5
    assert result.transcript == []
    assert result.duration == 10.5
    assert result.frames_extracted == 2
    assert result.error is None

    # Test with error
    error_result = VideoResult(
        keyframes=[],
        transcript=[],
        duration=0.0,
        frames_extracted=0,
        error="File not found"
    )
    assert error_result.error == "File not found"


def test_analyze_video_rejects_missing_file():
    """analyze_video should return error for nonexistent file."""
    result = analyze_video("/nonexistent/video.mp4")

    assert result.error is not None
    assert "not found" in result.error.lower() or "does not exist" in result.error.lower()
    assert result.frames_extracted == 0
    assert len(result.keyframes) == 0


def test_analyze_video_rejects_oversized_file():
    """analyze_video should reject files over 500MB in work mode."""
    # Mock file that exists but is too large (600MB)
    test_file = "/tmp/large_video.mp4"

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=600 * 1024 * 1024):
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 500
                mock_cfg.is_work_mode = True
                mock_config.return_value = mock_cfg

                result = analyze_video(test_file)

                assert result.error is not None
                assert "file size" in result.error.lower() or "too large" in result.error.lower()
                assert result.frames_extracted == 0


def test_analyze_video_rejects_long_duration():
    """analyze_video should reject videos longer than max_video_duration in work mode."""
    test_file = "/tmp/long_video.mp4"

    # Mock ffprobe output showing 700 second video (longer than 600s limit)
    mock_ffprobe_output = '{"format": {"duration": "700.5"}}'

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=100 * 1024 * 1024):  # 100MB
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 500
                mock_cfg.max_video_duration = 600
                mock_cfg.is_work_mode = True
                mock_config.return_value = mock_cfg

                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(
                        stdout=mock_ffprobe_output,
                        returncode=0
                    )

                    result = analyze_video(test_file)

                    assert result.error is not None
                    assert "duration" in result.error.lower() or "too long" in result.error.lower()


def test_ffmpeg_not_installed():
    """analyze_video should handle missing ffmpeg gracefully."""
    test_file = "/tmp/video.mp4"

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 500
                mock_cfg.max_video_duration = 600
                mock_cfg.is_work_mode = True
                mock_config.return_value = mock_cfg

                # Simulate ffprobe not found
                with patch('subprocess.run', side_effect=FileNotFoundError("ffprobe not found")):
                    result = analyze_video(test_file)

                    assert result.error is not None
                    assert "ffmpeg" in result.error.lower() or "ffprobe" in result.error.lower()
                    assert result.frames_extracted == 0


def test_analyze_video_success():
    """analyze_video should extract frames successfully."""
    test_file = "/tmp/video.mp4"

    # Mock ffprobe output
    mock_ffprobe_output = '{"format": {"duration": "10.5"}}'

    # Create mock frame images
    mock_frame1 = Image.new("RGB", (640, 480), color=(255, 0, 0))
    mock_frame2 = Image.new("RGB", (640, 480), color=(0, 255, 0))

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 500
                mock_cfg.max_video_duration = 600
                mock_cfg.is_work_mode = True
                mock_config.return_value = mock_cfg

                with patch('subprocess.run') as mock_run:
                    # First call: ffprobe
                    # Subsequent calls: ffmpeg frame extraction
                    mock_run.return_value = MagicMock(
                        stdout=mock_ffprobe_output,
                        returncode=0
                    )

                    with patch('screen_vision.video._extract_frames', return_value=[mock_frame1, mock_frame2]):
                        result = analyze_video(test_file, max_frames=20)

                        assert result.error is None
                        assert result.duration == 10.5
                        assert result.frames_extracted == 2
                        assert len(result.keyframes) == 2


def test_analyze_video_with_time_range():
    """analyze_video should respect start_time and end_time parameters."""
    test_file = "/tmp/video.mp4"

    # Mock ffprobe output
    mock_ffprobe_output = '{"format": {"duration": "60.0"}}'

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=10 * 1024 * 1024):
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 500
                mock_cfg.max_video_duration = 600
                mock_cfg.is_work_mode = True
                mock_config.return_value = mock_cfg

                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(
                        stdout=mock_ffprobe_output,
                        returncode=0
                    )

                    with patch('screen_vision.video._extract_frames', return_value=[]):
                        result = analyze_video(test_file, start_time=10.0, end_time=20.0)

                        # Should still succeed (even with no frames for this test)
                        assert result.error is None
                        assert result.duration == 60.0


def test_analyze_video_personal_mode_no_limits():
    """analyze_video should not enforce limits in personal mode."""
    test_file = "/tmp/huge_video.mp4"

    # Large file and long duration - should be OK in personal mode
    mock_ffprobe_output = '{"format": {"duration": "1000.0"}}'

    with patch('os.path.exists', return_value=True):
        with patch('os.path.getsize', return_value=2000 * 1024 * 1024):  # 2GB
            with patch('screen_vision.video.get_config') as mock_config:
                mock_cfg = MagicMock()
                mock_cfg.max_video_file_mb = 0  # 0 means unlimited in personal mode
                mock_cfg.max_video_duration = 0
                mock_cfg.is_work_mode = False
                mock_config.return_value = mock_cfg

                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(
                        stdout=mock_ffprobe_output,
                        returncode=0
                    )

                    with patch('screen_vision.video._extract_frames', return_value=[]):
                        result = analyze_video(test_file)

                        # Should not fail on size/duration checks
                        assert result.error is None or "size" not in result.error.lower()
