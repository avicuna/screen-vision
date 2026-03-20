"""Tests for image analysis module."""

import os
import tempfile
from unittest import mock

import pytest
from PIL import Image

from screen_vision.analyze import analyze_image, AnalyzeResult


def test_analyze_image_success():
    """Test successful image analysis."""
    # Create a temporary JPEG image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        image = Image.new("RGB", (100, 100), color="red")
        image.save(tmp_path, format="JPEG")

    try:
        # Analyze the image
        result = analyze_image(tmp_path)

        # Verify result
        assert result.error is None
        assert result.base64_image != ""
        assert result.resolution == "100x100"
        assert result.source == "file"
        assert result.file_name == os.path.basename(tmp_path)
        assert result.timestamp != ""
        assert result.security_redactions >= 0

    finally:
        # Clean up
        os.unlink(tmp_path)


def test_analyze_image_missing_file():
    """Test analysis of nonexistent file returns error."""
    result = analyze_image("/nonexistent/path/image.jpg")

    assert result.error is not None
    assert "File not found" in result.error
    assert result.base64_image == ""
    assert result.resolution == "0x0"


def test_analyze_image_oversized():
    """Test that oversized files are rejected."""
    # Create a temporary file
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        image = Image.new("RGB", (100, 100), color="blue")
        image.save(tmp_path, format="JPEG")

    try:
        # Mock getsize to return > 50MB
        with mock.patch("os.path.getsize", return_value=51 * 1024 * 1024):
            result = analyze_image(tmp_path)

        # Verify error
        assert result.error is not None
        assert "exceeds maximum" in result.error
        assert result.base64_image == ""

    finally:
        os.unlink(tmp_path)


def test_analyze_image_resizes_large():
    """Test that large images are resized to max 2048px."""
    # Create a large temporary image (4000x6000)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        image = Image.new("RGB", (4000, 6000), color="green")
        image.save(tmp_path, format="JPEG")

    try:
        result = analyze_image(tmp_path)

        # Verify image was resized
        assert result.error is None
        width, height = map(int, result.resolution.split("x"))
        assert max(width, height) <= 2048
        # Aspect ratio should be preserved
        assert abs((width / height) - (4000 / 6000)) < 0.01

    finally:
        os.unlink(tmp_path)


def test_analyze_image_strips_exif():
    """Test that EXIF data is stripped from output."""
    # Create an image with EXIF data
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        image = Image.new("RGB", (100, 100), color="yellow")
        # Add some fake EXIF data
        from PIL.Image import Exif

        exif = Exif()
        exif[0x0132] = "2024:01:01 12:00:00"  # DateTime tag
        image.save(tmp_path, format="JPEG", exif=exif.tobytes())

    try:
        result = analyze_image(tmp_path)

        # Decode the base64 image and check for EXIF
        import base64
        import io

        image_bytes = base64.b64decode(result.base64_image)
        output_image = Image.open(io.BytesIO(image_bytes))

        # Verify EXIF is stripped
        exif_data = output_image.getexif()
        assert len(exif_data) == 0 or exif_data is None

    finally:
        os.unlink(tmp_path)
