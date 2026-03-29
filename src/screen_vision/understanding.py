"""Vision LLM understanding — Google Lens-like screen analysis."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass

import httpx
from PIL import Image


@dataclass
class UnderstandingResult:
    """Result of Vision LLM understanding."""
    summary: str  # "VS Code showing booking.py with TypeError on line 42"
    application: dict  # {"name": "VS Code", "type": "code_editor"}
    tags: list[str]  # ["python", "error", "TypeError"]
    entities: list[dict]  # [{"type": "error", "value": "TypeError...", "details": {...}}]
    actionable_insights: list[str]  # ["The + operator is used on incompatible types"]
    full_text: str  # Complete OCR text
    confidence: float  # 0.0-1.0
    error: str | None = None
    latency_ms: int = 0


def _encode_image_to_base64(image: Image.Image, quality: int = 85) -> str:
    """
    Encode PIL Image to base64 JPEG string.

    Args:
        image: PIL Image to encode
        quality: JPEG quality (1-100, default 85 for better LLM reading)

    Returns:
        Base64-encoded JPEG string
    """
    buffer = io.BytesIO()
    # Convert to RGB if not already (handles RGBA, LA, P, L, CMYK, etc.)
    if image.mode != 'RGB':
        image = image.convert('RGB')

    # Downscale if very large (saves tokens/cost, LLM doesn't need 4K)
    max_dim = 1536
    if max(image.size) > max_dim:
        ratio = max_dim / max(image.size)
        image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.LANCZOS)

    image.save(buffer, format='JPEG', quality=quality)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


def _build_system_prompt(ocr_text: str, user_prompt: str = "") -> str:
    """
    Build the system prompt for the Vision LLM.

    Args:
        ocr_text: Pre-extracted OCR text
        user_prompt: Optional user's custom question

    Returns:
        Complete system prompt
    """
    base_prompt = """You are ScreenLens AI. Analyze this screenshot and return ONLY valid JSON:
{
  "summary": "one specific sentence describing what's on screen",
  "application": {"name": "exact app name", "type": "dashboard|code_editor|terminal|chat|browser|email|document|spreadsheet|other"},
  "tags": ["relevant", "tags"],
  "entities": [{"type": "error|metric|message|url|file|function", "value": "the entity", "details": {}}],
  "actionable_insights": ["specific actionable observations"],
  "confidence": 0.0-1.0
}

Be SPECIFIC. Not 'a code editor' but 'VS Code showing user.py with ImportError on line 42'.
Not 'a dashboard' but 'Grafana dashboard showing CPU at 87% with a spike at 2pm'."""

    if ocr_text:
        base_prompt += f"\n\nPre-extracted OCR text (use as ground truth for exact text values):\n---\n{ocr_text}\n---"

    if user_prompt:
        base_prompt += f"\n\nUser's question: {user_prompt}"

    return base_prompt


def _extract_json_from_response(content: str) -> dict:
    """
    Extract JSON from LLM response, handling markdown-wrapped JSON.

    Args:
        content: Raw LLM response content

    Returns:
        Parsed JSON dict

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed
    """
    # Try direct JSON parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code blocks
    json_match = re.search(r'```json\s*\n(.*?)\n```', content, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # Try extracting from any code block
    code_match = re.search(r'```\s*\n(.*?)\n```', content, re.DOTALL)
    if code_match:
        return json.loads(code_match.group(1))

    # Failed to extract JSON
    raise json.JSONDecodeError("Could not extract JSON from response", content, 0)


async def understand_image(
    image: Image.Image,
    ocr_text: str = "",
    prompt: str = "",
) -> UnderstandingResult:
    """
    Analyze an image with a Vision LLM. Returns structured understanding.

    Args:
        image: PIL Image to analyze
        ocr_text: Pre-extracted OCR text (optional, helps improve accuracy)
        prompt: User's custom question/prompt (optional)

    Returns:
        UnderstandingResult with structured analysis or error information
    """
    start_time = time.time()

    # Check for authentication — supports ANTHROPIC_API_KEY (standard) or ANTHROPIC_AUTH_TOKEN (legacy)
    auth_token = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not auth_token:
        return UnderstandingResult(
            summary="",
            application={"name": "unknown", "type": "other"},
            tags=[],
            entities=[],
            actionable_insights=[],
            full_text=ocr_text,
            confidence=0.0,
            error="Missing ANTHROPIC_API_KEY environment variable",
            latency_ms=int((time.time() - start_time) * 1000)
        )

    # Get configuration — defaults to Anthropic API; set GENAI_GATEWAY_URL to override
    gateway_url = os.environ.get("GENAI_GATEWAY_URL", "https://api.anthropic.com")
    model = os.environ.get("SCREEN_VISION_UNDERSTANDING_MODEL", "claude-sonnet-4-5-20241022")

    try:
        # Encode image to base64
        b64_image = _encode_image_to_base64(image)

        # Build prompt
        system_prompt = _build_system_prompt(ocr_text, prompt)

        # Make API call
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{gateway_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {auth_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": system_prompt},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}"
                            }}
                        ]
                    }]
                }
            )

        # Check for HTTP errors
        if resp.status_code != 200:
            error_msg = f"API returned status {resp.status_code}: {resp.text}"
            return UnderstandingResult(
                summary="",
                application={"name": "unknown", "type": "other"},
                tags=[],
                entities=[],
                actionable_insights=[],
                full_text=ocr_text,
                confidence=0.0,
                error=error_msg,
                latency_ms=int((time.time() - start_time) * 1000)
            )

        # Parse response
        response_data = resp.json()
        content = response_data["choices"][0]["message"]["content"]

        # Extract JSON from content
        result_json = _extract_json_from_response(content)

        # Validate and coerce response fields
        app = result_json.get("application", {})
        if not isinstance(app, dict):
            app = {"name": "unknown", "type": "other"}
        if "name" not in app:
            app["name"] = "unknown"
        if "type" not in app:
            app["type"] = "other"

        confidence = result_json.get("confidence", 0.0)
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        tags = result_json.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        entities = result_json.get("entities", [])
        if not isinstance(entities, list):
            entities = []

        insights = result_json.get("actionable_insights", [])
        if not isinstance(insights, list):
            insights = []

        return UnderstandingResult(
            summary=str(result_json.get("summary", "")),
            application=app,
            tags=tags,
            entities=entities,
            actionable_insights=insights,
            full_text=ocr_text,
            confidence=confidence,
            error=None,
            latency_ms=int((time.time() - start_time) * 1000)
        )

    except json.JSONDecodeError as e:
        return UnderstandingResult(
            summary="",
            application={"name": "unknown", "type": "other"},
            tags=[],
            entities=[],
            actionable_insights=[],
            full_text=ocr_text,
            confidence=0.0,
            error=f"Failed to parse JSON response: {str(e)}",
            latency_ms=int((time.time() - start_time) * 1000)
        )

    except Exception as e:
        return UnderstandingResult(
            summary="",
            application={"name": "unknown", "type": "other"},
            tags=[],
            entities=[],
            actionable_insights=[],
            full_text=ocr_text,
            confidence=0.0,
            error=f"LLM API call failed: {str(e)}",
            latency_ms=int((time.time() - start_time) * 1000)
        )
