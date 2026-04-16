"""Image analysis for file-based inputs (AirDrop, file drop)."""

import base64
import io
import os
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional

from PIL import Image

from screen_vision.config import get_config
from screen_vision.ocr import run_ocr, NoOcrEngineError
from screen_vision.security import SecurityScanner


@dataclass
class AnalyzeResult:
    """Result of image analysis."""

    base64_image: str
    resolution: str  # e.g., "1920x1080"
    source: str  # "file"
    file_name: str
    ocr_text: str
    timestamp: str  # ISO 8601 format
    security_redactions: int  # Count of PII redactions (work mode only)
    error: Optional[str] = None


def analyze_image(file_path: str, prompt: str = "") -> AnalyzeResult:
    """Analyze an image file with OCR and security scanning.

    Args:
        file_path: Path to the image file
        prompt: Optional analysis prompt (unused for now, reserved for future)

    Returns:
        AnalyzeResult with image data, OCR text, and security info

    Raises:
        ValueError: If file doesn't exist or exceeds size limits
    """
    config = get_config()

    # Validate file exists
    if not os.path.exists(file_path):
        return AnalyzeResult(
            base64_image="",
            resolution="0x0",
            source="file",
            file_name=os.path.basename(file_path),
            ocr_text="",
            timestamp=datetime.now(UTC).isoformat(),
            security_redactions=0,
            error=f"File not found: {file_path}",
        )

    # Check file size (max 50MB)
    max_size_bytes = 50 * 1024 * 1024
    file_size = os.path.getsize(file_path)
    if file_size > max_size_bytes:
        return AnalyzeResult(
            base64_image="",
            resolution="0x0",
            source="file",
            file_name=os.path.basename(file_path),
            ocr_text="",
            timestamp=datetime.now(UTC).isoformat(),
            security_redactions=0,
            error=f"File size {file_size} bytes exceeds maximum {max_size_bytes} bytes",
        )

    try:
        # Open and convert to RGB
        image = Image.open(file_path)
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Resize if too large (max 2048px on longest side)
        max_dimension = 2048
        width, height = image.size
        if max(width, height) > max_dimension:
            if width > height:
                new_width = max_dimension
                new_height = int(height * (max_dimension / width))
            else:
                new_height = max_dimension
                new_width = int(width * (max_dimension / height))
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Run OCR (optional — image analysis works without it)
        try:
            ocr_result = run_ocr(image)
            ocr_text = ocr_result.text
        except NoOcrEngineError:
            ocr_result = None
            ocr_text = ""

        # Security scan (work mode only)
        security_redactions = 0
        if config.is_work_mode:
            scanner = SecurityScanner(enabled=True)
            scan_result = scanner.scan_text(ocr_text)

            # Block if PCI or secrets detected
            if scan_result.should_block:
                return AnalyzeResult(
                    base64_image="",
                    resolution=f"{image.size[0]}x{image.size[1]}",
                    source="file",
                    file_name=os.path.basename(file_path),
                    ocr_text="",
                    timestamp=datetime.now(UTC).isoformat(),
                    security_redactions=0,
                    error=f"Security scan blocked: {len([f for f in scan_result.findings if f.action == 'BLOCK'])} sensitive items detected",
                )

            # Count PII redactions
            security_redactions = len(
                [f for f in scan_result.findings if f.action == "REDACT"]
            )

        # Encode as base64 JPEG (quality 75, EXIF stripped)
        buffer = io.BytesIO()
        image.save(
            buffer, format="JPEG", quality=config.default_jpeg_quality, exif=b""
        )
        buffer.seek(0)
        base64_image = base64.b64encode(buffer.read()).decode("utf-8")

        return AnalyzeResult(
            base64_image=base64_image,
            resolution=f"{image.size[0]}x{image.size[1]}",
            source="file",
            file_name=os.path.basename(file_path),
            ocr_text=ocr_text,
            timestamp=datetime.now(UTC).isoformat(),
            security_redactions=security_redactions,
            error=None,
        )

    except Exception as e:
        return AnalyzeResult(
            base64_image="",
            resolution="0x0",
            source="file",
            file_name=os.path.basename(file_path),
            ocr_text="",
            timestamp=datetime.now(UTC).isoformat(),
            security_redactions=0,
            error=f"Error processing image: {str(e)}",
        )
