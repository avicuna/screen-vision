"""Configuration management for Screen Vision MCP Server."""
import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Config:
    """Configuration for Screen Vision MCP Server.

    Supports two modes:
    - work: Strict corporate security with rate limits, denylist, and audit logs
    - personal: Relaxed mode with all security controls disabled
    """

    mode: Literal["work", "personal"]

    # Security controls
    enable_security_scan: bool
    enable_app_denylist: bool
    enable_audit_log: bool
    enable_call_detection: bool

    # Rate limits (0 = unlimited)
    max_captures_per_session: int
    min_capture_interval: float
    max_watch_duration: int
    max_video_file_mb: int
    max_video_duration: int
    max_frames_per_watch: int

    # Image quality
    default_jpeg_quality: int

    # Camera bridge settings
    camera_bridge_port: int
    camera_bridge_auto_shutdown_minutes: int
    camera_bridge_max_frame_size_bytes: int
    camera_bridge_max_fps: int

    @property
    def is_work_mode(self) -> bool:
        """Check if running in work mode."""
        return self.mode == "work"


# Preset configurations
_WORK_CONFIG = Config(
    mode="work",
    enable_security_scan=True,
    enable_app_denylist=True,
    enable_audit_log=True,
    enable_call_detection=True,
    max_captures_per_session=200,
    min_capture_interval=2.0,
    max_watch_duration=300,
    max_video_file_mb=500,
    max_video_duration=600,
    max_frames_per_watch=50,
    default_jpeg_quality=75,
    camera_bridge_port=8443,
    camera_bridge_auto_shutdown_minutes=10,
    camera_bridge_max_frame_size_bytes=1048576,
    camera_bridge_max_fps=10,
)

_PERSONAL_CONFIG = Config(
    mode="personal",
    enable_security_scan=False,
    enable_app_denylist=False,
    enable_audit_log=False,
    enable_call_detection=False,
    max_captures_per_session=0,
    min_capture_interval=0,
    max_watch_duration=0,
    max_video_file_mb=0,
    max_video_duration=0,
    max_frames_per_watch=0,
    default_jpeg_quality=75,
    camera_bridge_port=8443,
    camera_bridge_auto_shutdown_minutes=10,
    camera_bridge_max_frame_size_bytes=1048576,
    camera_bridge_max_fps=10,
)


def get_config() -> Config:
    """Get configuration based on SCREEN_VISION_MODE env var.

    Defaults to personal mode if not set.

    Returns:
        Config: The active configuration preset
    """
    mode = os.getenv("SCREEN_VISION_MODE", "personal").lower()

    if mode == "work":
        return _WORK_CONFIG
    else:
        return _PERSONAL_CONFIG
