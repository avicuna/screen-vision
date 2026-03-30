"""Version checking and update notification for screen-vision.

Checks PyPI for the latest version, caches the result for 24h,
and provides update status for MOTD and one-time nudges.

Works for both install methods:
- pip install (personal users) → nudge shows pip upgrade command
- uvx with --refresh (work/marketplace users) → nudge says restart Claude Code
"""
import json
import logging
import os
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
    is_uvx: bool = False
    error: str | None = None


def _get_current_version() -> str:
    """Get the installed version of screen-vision."""
    try:
        return get_installed_version(PACKAGE_NAME)
    except Exception:
        return "unknown"


def _is_running_under_uvx() -> bool:
    """Detect if the server was launched via uvx.

    uvx creates ephemeral venvs under ~/.cache/uv/. If our package is
    installed there, we're running under uvx.
    """
    try:
        # Check if the package location is inside a uv cache directory
        from importlib.metadata import packages_distributions
        import screen_vision
        pkg_path = str(Path(screen_vision.__file__).resolve())
        uv_cache = str(Path.home() / ".cache" / "uv")
        if uv_cache in pkg_path:
            return True
        # Also check UV_CACHE_DIR env var
        uv_cache_env = os.environ.get("UV_CACHE_DIR", "")
        if uv_cache_env and uv_cache_env in pkg_path:
            return True
    except Exception:
        pass
    return False


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
    """Fetch latest version from PyPI. Returns None on any failure.

    Public PyPI is the canonical source — both GitHub Actions and GitLab CI
    publish the same version from the same git tag.
    """
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
    is_uvx = _is_running_under_uvx()

    # Try cache first
    cached = _read_cache()
    if cached:
        latest = cached["latest"]
        return UpdateStatus(
            current=current,
            latest=latest,
            update_available=_parse_version(latest) > _parse_version(current),
            is_uvx=is_uvx,
        )

    # Cache miss — fetch from PyPI
    latest = _fetch_latest_version()
    if latest is None:
        return UpdateStatus(
            current=current,
            latest=None,
            update_available=False,
            is_uvx=is_uvx,
            error="Could not reach PyPI",
        )

    _write_cache(latest)
    return UpdateStatus(
        current=current,
        latest=latest,
        update_available=_parse_version(latest) > _parse_version(current),
        is_uvx=is_uvx,
    )


def format_update_notice(status: UpdateStatus) -> str | None:
    """Format a human-readable update notice. Returns None if up to date.

    Adapts the upgrade command based on install method:
    - uvx (marketplace): restart Claude Code (--refresh handles the rest)
    - pip (personal): pip install --upgrade screen-vision
    """
    if not status.update_available or not status.latest:
        return None
    if status.is_uvx:
        return (
            f"Update available: screen-vision {status.current} → {status.latest}. "
            f"Restart Claude Code to get the latest version."
        )
    return (
        f"Update available: screen-vision {status.current} → {status.latest}. "
        f"Run: pip install --upgrade screen-vision"
    )
