"""Security scanning pipeline for PII/PCI/secrets detection."""

import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw


@dataclass
class Finding:
    """Security finding in scanned text."""

    finding_type: str  # "PCI", "PII", "SECRET"
    pattern_name: str  # e.g., "visa_card", "email", "github_token"
    matched_text: str  # the matched text (masked for display)
    action: str  # "BLOCK" or "REDACT"
    _original_text: str = ""  # original unmasked text for matching


@dataclass
class ScanResult:
    """Result of security scan."""

    findings: list[Finding]
    should_block: bool  # True if any BLOCK finding
    should_redact: bool  # True if any REDACT finding
    is_clean: bool  # No findings at all


def _luhn_check(number: str) -> bool:
    """Validate credit card number with Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


# Application deny-list
BLOCKED_APPS = [
    "Slack",
    "Microsoft Teams",
    "Mail",
    "Outlook",
    "1Password",
    "LastPass",
    "Bitwarden",
    "Keychain Access",
    "Messages",
]


# Pre-compiled regex patterns for performance (Issue #7)
_PCI_PATTERNS_COMPILED = {
    "visa_card": re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"),
    "mastercard": re.compile(r"\b5[1-5][0-9]{14}\b"),
    "amex": re.compile(r"\b3[47][0-9]{13}\b"),
    "discover": re.compile(r"\b6(?:011|5[0-9]{2})[0-9]{12}\b"),
}

_PII_PATTERNS_COMPILED = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # Fixed [A-Z|a-z] bug
    "phone": re.compile(r"(?:tel:|phone:|call:|mobile:|☎|\+)\s*[0-9][\d\s\-().]{7,15}"),
    "private_ip": re.compile(r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"),  # Fixed 10.x.x.x bug
}

_SECRETS_PATTERNS_COMPILED = {
    "password_assignment": re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    "api_key_assignment": re.compile(r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*\S+"),
    "github_token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"),
    "gitlab_token": re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "vault_token": re.compile(r"(?:hvs\.|s\.)[A-Za-z0-9]{20,}"),
    "bearer_token": re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-._~+/]+=*"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "slack_token": re.compile(r"xox[bpsa]-[A-Za-z0-9\-]{10,}"),
    "ssh_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "gcp_key": re.compile(r'"private_key"\s*:\s*"-----BEGIN'),
    "db_connection": re.compile(r"(?i)(?:mysql|postgres|mongodb|redis)://[^:]+:[^@]+@"),
    "authorization_header": re.compile(r"(?i)authorization\s*:\s*\S+"),
}


class SecurityScanner:
    """Scans text and images for PII, PCI, and secrets."""

    # Keep pattern dictionaries for backwards compatibility (point to compiled versions)
    PCI_PATTERNS = _PCI_PATTERNS_COMPILED
    PII_PATTERNS = _PII_PATTERNS_COMPILED
    SECRETS_PATTERNS = _SECRETS_PATTERNS_COMPILED

    def __init__(self, enabled: bool = True):
        """Initialize security scanner.

        Args:
            enabled: If False, scanner is disabled and returns clean results
        """
        self.enabled = enabled

    def scan_text(self, text: str) -> ScanResult:
        """Scan text for PII/PCI/secrets. Returns findings with actions.

        Args:
            text: Text to scan

        Returns:
            ScanResult with findings and action flags
        """
        if not self.enabled:
            return ScanResult(
                findings=[],
                should_block=False,
                should_redact=False,
                is_clean=True,
            )

        findings = []

        # Normalize text for PCI scanning: remove spaces and dashes (Issue #5)
        normalized_text = text.replace(" ", "").replace("-", "")

        # Scan for PCI (credit cards) - BLOCK action
        for pattern_name, pattern in self.PCI_PATTERNS.items():
            for match in pattern.finditer(normalized_text):
                number = match.group()
                # Validate with Luhn algorithm
                if _luhn_check(number):
                    findings.append(
                        Finding(
                            finding_type="PCI",
                            pattern_name=pattern_name,
                            matched_text=self._mask_text(number),
                            action="BLOCK",
                            _original_text=number,
                        )
                    )

        # Scan for PII - REDACT action
        for pattern_name, pattern in self.PII_PATTERNS.items():
            for match in pattern.finditer(text):
                matched = match.group()
                findings.append(
                    Finding(
                        finding_type="PII",
                        pattern_name=pattern_name,
                        matched_text=self._mask_text(matched),
                        action="REDACT",
                        _original_text=matched,
                    )
                )

        # Scan for secrets - BLOCK action
        for pattern_name, pattern in self.SECRETS_PATTERNS.items():
            for match in pattern.finditer(text):
                matched = match.group()
                findings.append(
                    Finding(
                        finding_type="SECRET",
                        pattern_name=pattern_name,
                        matched_text=self._mask_text(matched),
                        action="BLOCK",
                        _original_text=matched,
                    )
                )

        # Determine result flags
        should_block = any(f.action == "BLOCK" for f in findings)
        should_redact = any(f.action == "REDACT" for f in findings)
        is_clean = len(findings) == 0

        return ScanResult(
            findings=findings,
            should_block=should_block,
            should_redact=should_redact,
            is_clean=is_clean,
        )

    def is_app_blocked(self, app_name: str) -> bool:
        """Check if app is in the deny-list.

        Args:
            app_name: Application name to check

        Returns:
            True if app is blocked
        """
        # Case-insensitive substring matching (Issue #6)
        return any(blocked.lower() in app_name.lower() for blocked in BLOCKED_APPS)

    def _mask_text(self, text: str, show_chars: int = 4) -> str:
        """Mask sensitive text for display.

        Args:
            text: Text to mask
            show_chars: Number of characters to show at start/end

        Returns:
            Masked text like "4111...1111"
        """
        if len(text) <= show_chars * 2:
            return "*" * len(text)
        return f"{text[:show_chars]}...{text[-show_chars:]}"


def redact_image(
    image: Image.Image, ocr_data: list[dict[str, Any]], findings: list[Finding]
) -> Image.Image:
    """Draw black rectangles over regions containing findings.

    Args:
        image: Source image to redact
        ocr_data: OCR results with text and bounding boxes
        findings: Security findings to redact

    Returns:
        New image with redacted regions
    """
    # Create a copy to avoid modifying original
    redacted = image.copy()
    draw = ImageDraw.Draw(redacted)

    # Find matching OCR regions and redact them (Issues #1 and #2)
    for ocr_item in ocr_data:
        bbox = ocr_item.get("bbox")
        ocr_text = ocr_item.get("text", "")

        if not bbox or not ocr_text:
            continue

        # Normalize OCR text for matching (remove spaces/dashes for credit card matching)
        normalized_ocr = ocr_text.replace(" ", "").replace("-", "")

        # Check if this specific OCR region contains any finding
        should_redact = False
        for finding in findings:
            if finding.action not in ("BLOCK", "REDACT"):
                continue

            # Use _original_text for matching
            original = finding._original_text
            if not original:
                continue

            # Check both original and normalized text
            if original in ocr_text or original in normalized_ocr:
                should_redact = True
                break

        if should_redact:
            # Draw black rectangle over the bounding box
            draw.rectangle(bbox, fill="black")

    return redacted
