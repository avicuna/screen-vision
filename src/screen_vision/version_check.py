"""Version checking and update notification for screen-vision.

Checks PyPI for the latest version, caches the result for 24h,
and provides update status for MOTD and one-time nudges.
"""
import json
import logging
import time
from dataclasses import dataclass
from importlib.metadata import version as get_installed_version
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("screen_vision")

PACKAGE_NAME = "screen-vision"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CACHE_DIR = Path.home() / ".screen-vision"
CACHE_FILE = CACHE_DIR / "version_cache.json"
CACHE_TTL = 86400  # 24 hours


@dataclass
class UpdateStatus:
    """Result of a version check."""
    current: str
    latest: str | None
    update_available: bool
    error: str | None = None


def _get_current_version() -> str:
    """Get the installed version of screen-vision."""
    try:
        return get_installed_version(PACKAGE_NAME)
    except Exception:
        return "unknown"


def _read_cache() -> dict | None:
    """Read cached version info if fresh."""
    try:
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("checked_at", 0) < CACHE_TTL:
            return data
        return None  # stale
    except Exception:
        return None


def _write_cache(latest: str) -> None:
    """Cache the latest version from PyPI."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({
            "latest": latest,
            "checked_at": time.time(),
        }))
    except Exception:
        pass  # non-critical


def _fetch_latest_version() -> str | None:
    """Fetch latest version from PyPI. Returns None on any failure."""
    try:
        req = Request(PYPI_URL, headers={"Accept": "application/json"})
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except (URLError, KeyError, json.JSONDecodeError, OSError):
        return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


def get_update_status() -> UpdateStatus:
    """Check if an update is available. Uses 24h cache to avoid repeated PyPI hits.

    This is safe to call at startup — it returns instantly from cache most of
    the time, and the PyPI fetch has a 3s timeout for the cold path.
    """
    current = _get_current_version()

    # Try cache first
    cached = _read_cache()
    if cached:
        latest = cached["latest"]
        return UpdateStatus(
            current=current,
            latest=latest,
            update_available=_parse_version(latest) > _parse_version(current),
        )

    # Cache miss — fetch from PyPI
    latest = _fetch_latest_version()
    if latest is None:
        return UpdateStatus(
            current=current,
            latest=None,
            update_available=False,
            error="Could not reach PyPI",
        )

    _write_cache(latest)
    return UpdateStatus(
        current=current,
        latest=latest,
        update_available=_parse_version(latest) > _parse_version(current),
    )


def format_update_notice(status: UpdateStatus) -> str | None:
    """Format a human-readable update notice. Returns None if up to date."""
    if not status.update_available or not status.latest:
        return None
    return (
        f"Update available: screen-vision {status.current} → {status.latest}. "
        f"Run: pip install --upgrade screen-vision"
    )
