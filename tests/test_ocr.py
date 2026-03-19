"""Tests for OCR module."""
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import numpy as np
from screen_vision.ocr import OcrResult, run_ocr, extract_text_near


def test_run_ocr_returns_result(sample_image):
    """run_ocr should return OcrResult with text, blocks, and confidence."""
    # Mock pytesseract functions
    mock_data = {
        'text': ['Hello', 'World', '', 'Test'],
        'conf': [95.5, 89.2, -1, 92.0],
        'left': [10, 50, 0, 100],
        'top': [10, 10, 0, 30],
        'width': [30, 35, 0, 25],
        'height': [15, 15, 0, 15]
    }

    with patch('screen_vision.ocr.pytesseract') as mock_pytesseract:
        mock_pytesseract.image_to_data.return_value = mock_data
        mock_pytesseract.image_to_string.return_value = "Hello World Test"

        result = run_ocr(sample_image)

        assert isinstance(result, OcrResult)
        assert result.text == "Hello World Test"
        assert len(result.blocks) == 3  # Empty text and -1 confidence should be filtered
        assert result.blocks[0]['text'] == 'Hello'
        assert result.blocks[0]['confidence'] == 95.5
        assert 'bbox' in result.blocks[0]
        assert result.blocks[0]['bbox'] == {'x': 10, 'y': 10, 'width': 30, 'height': 15}
        # Average confidence: (95.5 + 89.2 + 92.0) / 3 = 92.23...
        assert 92.0 < result.average_confidence < 93.0


def test_extract_text_near_cursor():
    """extract_text_near should return text from blocks near the cursor."""
    blocks = [
        {'text': 'Near', 'bbox': {'x': 100, 'y': 100, 'width': 40, 'height': 20}},
        {'text': 'Text', 'bbox': {'x': 150, 'y': 100, 'width': 40, 'height': 20}},
        {'text': 'Far', 'bbox': {'x': 500, 'y': 500, 'width': 40, 'height': 20}},
    ]
    cursor = {'x': 120, 'y': 110}

    result = extract_text_near(blocks, cursor, radius=200)

    assert 'Near' in result
    assert 'Text' in result
    assert 'Far' not in result


def test_extract_text_near_excludes_far():
    """extract_text_near should exclude blocks far from cursor."""
    blocks = [
        {'text': 'Close', 'bbox': {'x': 100, 'y': 100, 'width': 40, 'height': 20}},
        {'text': 'VeryFar', 'bbox': {'x': 1000, 'y': 1000, 'width': 40, 'height': 20}},
    ]
    cursor = {'x': 110, 'y': 110}

    result = extract_text_near(blocks, cursor, radius=50)

    assert 'Close' in result
    assert 'VeryFar' not in result


def test_ocr_graceful_when_tesseract_missing(sample_image):
    """run_ocr should return empty result when pytesseract is not installed."""
    with patch('screen_vision.ocr.pytesseract', None):
        result = run_ocr(sample_image)

        assert isinstance(result, OcrResult)
        assert result.text == ""
        assert result.blocks == []
        assert result.average_confidence == 0.0
