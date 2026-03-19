"""Video analysis for Screen Vision.

Handles video file processing, keyframe extraction, and optional transcription.
Uses ffmpeg/ffprobe for video processing. Gracefully degrades when not available.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from screen_vision.config import get_config


@dataclass
class VideoResult:
    """Result from video analysis with extracted frames and optional transcript."""

    keyframes: list  # list of dicts with image, timestamp, etc.
    transcript: list  # TranscriptSegments if audio extracted
    duration: float
    frames_extracted: int
    error: str | None = None


def analyze_video(
    file_path: str,
    start_time: float = 0,
    end_time: float | None = None,
    max_frames: int = 20,
) -> VideoResult:
    """Analyze a local video file — extract key frames and optionally transcribe.

    Args:
        file_path: Path to the video file to analyze
        start_time: Start time in seconds (default: 0)
        end_time: End time in seconds (default: None = entire video)
        max_frames: Maximum number of frames to extract (default: 20)

    Returns:
        VideoResult with extracted keyframes, transcript, and metadata

    Implementation flow:
    1. Validate file exists
    2. Check file size against config limit (500MB in work mode)
    3. Get video duration via ffprobe
    4. Check duration against config limit (600s in work mode)
    5. Extract frames at scene changes
    6. If fewer than max_frames, supplement with periodic samples
    7. Load extracted frames as PIL Images
    8. Optionally extract audio and transcribe
    9. Return VideoResult
    """
    # 1. Validate file exists
    if not os.path.exists(file_path):
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=0.0,
            frames_extracted=0,
            error=f"File not found: {file_path}",
        )

    # 2. Check file size against config limit
    config = get_config()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    if config.max_video_file_mb > 0 and file_size_mb > config.max_video_file_mb:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=0.0,
            frames_extracted=0,
            error=f"File size ({file_size_mb:.1f}MB) exceeds limit ({config.max_video_file_mb}MB)",
        )

    # 3. Get video duration via ffprobe
    try:
        duration = _get_video_duration(file_path)
    except FileNotFoundError:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=0.0,
            frames_extracted=0,
            error="ffprobe not found. Install ffmpeg to analyze videos.",
        )
    except Exception as e:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=0.0,
            frames_extracted=0,
            error=f"Failed to get video duration: {e}",
        )

    # 4. Check duration against config limit
    if config.max_video_duration > 0 and duration > config.max_video_duration:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=duration,
            frames_extracted=0,
            error=f"Video duration ({duration:.1f}s) exceeds limit ({config.max_video_duration}s)",
        )

    # 5-7. Extract frames
    try:
        frames = _extract_frames(
            file_path=file_path,
            start_time=start_time,
            end_time=end_time,
            max_frames=max_frames,
            duration=duration,
        )
    except FileNotFoundError:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=duration,
            frames_extracted=0,
            error="ffmpeg not found. Install ffmpeg to analyze videos.",
        )
    except Exception as e:
        return VideoResult(
            keyframes=[],
            transcript=[],
            duration=duration,
            frames_extracted=0,
            error=f"Failed to extract frames: {e}",
        )

    # 8. Build keyframes list with timestamps
    # For now, we'll space frames evenly across the time range
    effective_start = start_time
    effective_end = end_time if end_time is not None else duration
    time_range = effective_end - effective_start

    keyframes = []
    for i, frame in enumerate(frames):
        # Calculate timestamp for this frame
        if len(frames) > 1:
            timestamp = effective_start + (i * time_range / (len(frames) - 1))
        else:
            timestamp = effective_start + time_range / 2

        keyframes.append({"image": frame, "timestamp": timestamp})

    # 9. TODO: Optionally extract audio and transcribe (future enhancement)
    transcript = []

    return VideoResult(
        keyframes=keyframes,
        transcript=transcript,
        duration=duration,
        frames_extracted=len(frames),
        error=None,
    )


def _get_video_duration(file_path: str) -> float:
    """Get video duration in seconds using ffprobe.

    Args:
        file_path: Path to the video file

    Returns:
        Duration in seconds

    Raises:
        FileNotFoundError: If ffprobe is not installed
        subprocess.SubprocessError: If ffprobe command fails
        ValueError: If duration cannot be parsed
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        file_path,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=10, check=True
    )

    data = json.loads(result.stdout)
    duration_str = data.get("format", {}).get("duration")

    if duration_str is None:
        raise ValueError("Could not parse video duration from ffprobe output")

    return float(duration_str)


def _extract_frames(
    file_path: str,
    start_time: float,
    end_time: float | None,
    max_frames: int,
    duration: float,
) -> list[Image.Image]:
    """Extract frames from video using ffmpeg.

    Attempts to extract frames at scene changes first. If fewer than max_frames
    are extracted, supplements with periodic samples.

    Args:
        file_path: Path to the video file
        start_time: Start time in seconds
        end_time: End time in seconds (None = entire video)
        max_frames: Maximum number of frames to extract
        duration: Total video duration in seconds

    Returns:
        List of PIL Images

    Raises:
        FileNotFoundError: If ffmpeg is not installed
        subprocess.SubprocessError: If ffmpeg command fails
    """
    frames = []

    # Create temporary directory for frame extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        output_pattern = os.path.join(temp_dir, "frame_%04d.png")

        # Calculate effective time range
        effective_end = end_time if end_time is not None else duration
        clip_duration = effective_end - start_time

        # Build ffmpeg command for scene detection
        # Extract frames at scene changes (threshold 0.3)
        cmd = [
            "ffmpeg",
            "-ss",
            str(start_time),  # Seek to start time
            "-i",
            file_path,
            "-vf",
            "select='gt(scene,0.3)',scale=640:-1",  # Scene detection + scale to reasonable size
            "-vsync",
            "vfn",  # Variable frame number
            "-frames:v",
            str(max_frames),  # Limit frames
            output_pattern,
        ]

        if end_time is not None:
            # Insert duration limit after -ss
            cmd.insert(3, "-t")
            cmd.insert(4, str(clip_duration))

        # Run ffmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Check for errors (but some warnings are OK)
        if result.returncode != 0:
            # If scene detection fails, try periodic sampling instead
            frames = _extract_periodic_frames(
                file_path, start_time, effective_end, max_frames, temp_dir
            )
        else:
            # Load extracted frames
            frame_files = sorted(Path(temp_dir).glob("frame_*.png"))
            frames = [Image.open(f) for f in frame_files]

        # If we got fewer frames than requested, supplement with periodic samples
        if len(frames) < max_frames and len(frames) < 10:
            periodic_frames = _extract_periodic_frames(
                file_path, start_time, effective_end, max_frames, temp_dir
            )
            # Add periodic frames if we didn't get enough from scene detection
            if len(periodic_frames) > len(frames):
                frames = periodic_frames

    return frames[:max_frames]  # Ensure we don't exceed max_frames


def _extract_periodic_frames(
    file_path: str,
    start_time: float,
    end_time: float,
    max_frames: int,
    temp_dir: str,
) -> list[Image.Image]:
    """Extract frames at regular intervals as a fallback.

    Args:
        file_path: Path to the video file
        start_time: Start time in seconds
        end_time: End time in seconds
        max_frames: Maximum number of frames to extract
        temp_dir: Temporary directory for frame extraction

    Returns:
        List of PIL Images

    Raises:
        FileNotFoundError: If ffmpeg is not installed
    """
    frames = []
    clip_duration = end_time - start_time

    # Calculate FPS for periodic sampling
    fps = max_frames / clip_duration if clip_duration > 0 else 1

    output_pattern = os.path.join(temp_dir, "periodic_%04d.png")

    cmd = [
        "ffmpeg",
        "-ss",
        str(start_time),
        "-i",
        file_path,
        "-t",
        str(clip_duration),
        "-vf",
        f"fps={fps},scale=640:-1",
        "-frames:v",
        str(max_frames),
        output_pattern,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0:
        # Load extracted frames
        frame_files = sorted(Path(temp_dir).glob("periodic_*.png"))
        frames = [Image.open(f) for f in frame_files]

    return frames
