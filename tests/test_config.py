"""Tests for configuration module."""
import os
import pytest
from screen_vision.config import Config, get_config


def test_default_mode_is_personal(monkeypatch):
    """Default mode should be personal when no env var is set."""
    monkeypatch.delenv("SCREEN_VISION_MODE", raising=False)
    config = get_config()
    assert config.mode == "personal"
    assert not config.is_work_mode


def test_work_mode_from_env_var(monkeypatch, work_env):
    """Work mode should enable security scanning, app denylist, audit logging, and call detection."""
    for key, value in work_env.items():
        monkeypatch.setenv(key, value)

    config = get_config()
    assert config.mode == "work"
    assert config.is_work_mode
    assert config.enable_security_scan is True
    assert config.enable_app_denylist is True
    assert config.enable_audit_log is True
    assert config.enable_call_detection is True


def test_personal_mode_disables_security_controls(monkeypatch, personal_env):
    """Personal mode should disable all security controls."""
    for key, value in personal_env.items():
        monkeypatch.setenv(key, value)

    config = get_config()
    assert config.mode == "personal"
    assert not config.is_work_mode
    assert config.enable_security_scan is False
    assert config.enable_app_denylist is False
    assert config.enable_audit_log is False
    assert config.enable_call_detection is False


def test_work_mode_rate_limits(monkeypatch, work_env):
    """Work mode should have strict rate limits."""
    for key, value in work_env.items():
        monkeypatch.setenv(key, value)

    config = get_config()
    assert config.max_captures_per_session == 200
    assert config.min_capture_interval == 2.0
    assert config.max_watch_duration == 300
    assert config.max_video_file_mb == 500
    assert config.max_video_duration == 600
    assert config.max_frames_per_watch == 50


def test_personal_mode_unlimited_limits(monkeypatch, personal_env):
    """Personal mode should have all limits set to 0 (unlimited)."""
    for key, value in personal_env.items():
        monkeypatch.setenv(key, value)

    config = get_config()
    assert config.max_captures_per_session == 0
    assert config.min_capture_interval == 0
    assert config.max_watch_duration == 0
    assert config.max_video_file_mb == 0
    assert config.max_video_duration == 0
    assert config.max_frames_per_watch == 0


def test_is_work_mode_property(monkeypatch):
    """is_work_mode property should correctly identify work mode."""
    # Test work mode
    monkeypatch.setenv("SCREEN_VISION_MODE", "work")
    config = get_config()
    assert config.is_work_mode is True

    # Test personal mode
    monkeypatch.setenv("SCREEN_VISION_MODE", "personal")
    config = get_config()
    assert config.is_work_mode is False
