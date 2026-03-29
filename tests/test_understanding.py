"""Tests for Vision LLM understanding module."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from PIL import Image
import numpy as np
import json

from screen_vision.understanding import understand_image, UnderstandingResult


@pytest.fixture
def sample_image():
    """Create a simple test image."""
    return Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8))


@pytest.fixture
def mock_llm_response():
    """Mock successful LLM response."""
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "summary": "VS Code showing main.py with syntax error",
                    "application": {"name": "VS Code", "type": "code_editor"},
                    "tags": ["python", "error"],
                    "entities": [{"type": "error", "value": "SyntaxError", "details": {}}],
                    "actionable_insights": ["Missing colon after if statement"],
                    "confidence": 0.92
                })
            }
        }]
    }


@pytest.mark.asyncio
async def test_understand_image_success(sample_image, mock_llm_response):
    """Test successful image understanding."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_response

    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            result = await understand_image(sample_image, ocr_text="def main():")

    assert result.error is None
    assert result.summary == "VS Code showing main.py with syntax error"
    assert result.application["type"] == "code_editor"
    assert len(result.tags) > 0
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_understand_image_with_prompt(sample_image, mock_llm_response):
    """Test understanding with custom user prompt."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_response

    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            await understand_image(sample_image, prompt="what error is this?")

    # Verify prompt was included in the request
    call_args = mock_client.post.call_args
    body = call_args.kwargs.get("json") or call_args[1].get("json")
    content = body["messages"][0]["content"]
    text_parts = [c["text"] for c in content if c["type"] == "text"]
    assert any("what error is this?" in t for t in text_parts)


@pytest.mark.asyncio
async def test_understand_image_llm_failure(sample_image):
    """Test graceful handling of LLM API failure."""
    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            result = await understand_image(sample_image, ocr_text="some text")

    assert result.error is not None
    assert result.full_text == "some text"  # OCR text preserved even on failure


@pytest.mark.asyncio
async def test_understand_image_no_auth(sample_image):
    """Test error when no auth token is provided."""
    with patch.dict("os.environ", {}, clear=True):
        result = await understand_image(sample_image)
    assert result.error is not None
    assert "anthropic_api_key" in result.error.lower()


@pytest.mark.asyncio
async def test_understanding_result_dataclass():
    """Test UnderstandingResult dataclass properties."""
    result = UnderstandingResult(
        summary="test", application={"name": "test", "type": "other"},
        tags=["test"], entities=[], actionable_insights=[],
        full_text="hello", confidence=0.5,
    )
    assert result.summary == "test"
    assert result.error is None


@pytest.mark.asyncio
async def test_understand_image_json_parsing_markdown_wrapped(sample_image):
    """Test parsing JSON that's wrapped in markdown code blocks."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": """```json
{
    "summary": "Terminal showing build error",
    "application": {"name": "Terminal", "type": "terminal"},
    "tags": ["build", "error"],
    "entities": [],
    "actionable_insights": ["Check compiler version"],
    "confidence": 0.85
}
```"""
            }
        }]
    }

    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            result = await understand_image(sample_image)

    assert result.error is None
    assert result.summary == "Terminal showing build error"
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_understand_image_invalid_json(sample_image):
    """Test handling of invalid JSON response from LLM."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": "This is not valid JSON at all!"
            }
        }]
    }

    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            result = await understand_image(sample_image, ocr_text="preserved text")

    assert result.error is not None
    assert "json" in result.error.lower() or "parse" in result.error.lower()
    assert result.full_text == "preserved text"


@pytest.mark.asyncio
async def test_understand_image_http_error(sample_image):
    """Test handling of HTTP error responses."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch("screen_vision.understanding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "test", "GENAI_GATEWAY_URL": "https://test"}):
            result = await understand_image(sample_image)

    assert result.error is not None
    assert "401" in result.error or "unauthorized" in result.error.lower()
