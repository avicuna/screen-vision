"""Audio recording and transcription for Screen Vision.

Handles microphone recording, call detection, and speech-to-text transcription.
Gracefully degrades when optional dependencies (sounddevice, faster-whisper) are missing.
"""

import subprocess
from dataclasses import dataclass
from typing import Any

import numpy as np

# Try to import optional audio dependencies
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    WHISPER_AVAILABLE = False


@dataclass
class TranscriptSegment:
    """A segment of transcribed speech with timing information."""

    text: str
    start_time: float  # seconds
    end_time: float  # seconds
    nearest_frame_index: int | None = None


# Call detection constants
CALL_APPS = ["zoom.us", "Microsoft Teams", "Slack", "FaceTime", "Google Chrome"]


class AudioRecorder:
    """Records audio from microphone and transcribes with Whisper."""

    def __init__(self, sample_rate: int = 16000):
        """Initialize audio recorder.

        Args:
            sample_rate: Audio sample rate in Hz (default 16000 for Whisper)
        """
        self.sample_rate = sample_rate
        self.buffer: np.ndarray | None = None
        self._recording = False

    def start(self, duration_seconds: float) -> None:
        """Record microphone audio for specified duration.

        Args:
            duration_seconds: How long to record in seconds

        Raises:
            RuntimeError: If sounddevice is not installed

        Stores audio in self.buffer as mono float32 numpy array.
        This is a blocking call - it waits for the full duration.
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(
                "sounddevice not installed. Install with: pip install sounddevice"
            )

        # Calculate number of frames needed
        frames = int(duration_seconds * self.sample_rate)

        # Record audio - blocks until complete
        self._recording = True
        recording = sd.rec(
            frames=frames,
            samplerate=self.sample_rate,
            channels=1,  # mono
            dtype='float32'
        )
        sd.wait()  # Wait for recording to complete
        self._recording = False

        # Store as flattened mono array
        self.buffer = recording.flatten()

    def stop(self) -> None:
        """Stop recording if active.

        This is safe to call even if not recording.
        """
        if self._recording:
            # In blocking mode, this doesn't do much, but provided for API consistency
            self._recording = False

    def transcribe(self) -> list[TranscriptSegment]:
        """Transcribe audio buffer using Whisper.

        Returns:
            List of TranscriptSegment objects with timestamped text.
            Empty list if Whisper is not installed or buffer is empty.

        Requires faster-whisper to be installed. Falls back gracefully if missing.
        """
        if not WHISPER_AVAILABLE:
            return []

        if self.buffer is None or len(self.buffer) == 0:
            return []

        # Load Whisper model (tiny model for speed, can be configured)
        model = WhisperModel("tiny", device="cpu", compute_type="int8")

        # Transcribe with word-level timestamps
        segments_iter, _ = model.transcribe(
            self.buffer,
            language="en",
            word_timestamps=False  # Segment-level is sufficient
        )

        # Convert to our TranscriptSegment format
        segments = []
        for segment in segments_iter:
            segments.append(
                TranscriptSegment(
                    text=segment.text,
                    start_time=segment.start,
                    end_time=segment.end
                )
            )

        return segments

    def clear(self) -> None:
        """Clear the audio buffer (zero-persistence)."""
        self.buffer = None


def is_call_active() -> bool:
    """Check if any call application is using the microphone.

    Returns:
        True if a known call app is using the mic, False otherwise.

    Gracefully returns False if process checking fails.
    """
    try:
        processes = _get_mic_using_processes()
        return any(p["name"] in CALL_APPS for p in processes)
    except Exception:
        # Fail gracefully - assume no call active
        return False


def _get_mic_using_processes() -> list[dict[str, Any]]:
    """Get processes currently using the microphone.

    Returns:
        List of dicts with "name" and "pid" keys.

    Raises:
        subprocess.SubprocessError: If lsof command fails
    """
    try:
        # On macOS, check which processes are accessing audio devices
        # Using lsof to find processes with audio file descriptors
        result = subprocess.run(
            ["lsof", "-c", "zoom", "-c", "Teams", "-c", "Slack", "-c", "FaceTime", "-c", "Chrome"],
            capture_output=True,
            text=True,
            timeout=5
        )

        processes = []
        if result.returncode == 0 and result.stdout:
            # Parse lsof output
            lines = result.stdout.strip().split('\n')
            seen_pids = set()

            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 2:
                    command = parts[0]
                    pid = parts[1]

                    if pid not in seen_pids:
                        seen_pids.add(pid)
                        processes.append({
                            "name": command,
                            "pid": int(pid) if pid.isdigit() else 0
                        })

        return processes

    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        # lsof not available or failed - return empty list
        return []
