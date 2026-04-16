"""Tests for OCR module."""
import pytest
from unittest.mock import patch, MagicMock, Mock
from PIL import Image
import numpy as np
from screen_vision.ocr import OcrResult, run_ocr, extract_text_near


def test_run_ocr_returns_result(sample_image):
    """run_ocr should return OcrResult with text, blocks, and confidence."""
    # Mock PaddleOCR result format: [[[box_points], (text, confidence)], ...]
    mock_paddle_result = [
        [[[10, 10], [40, 10], [40, 25], [10, 25]], ("Hello", 0.955)],
        [[[50, 10], [85, 10], [85, 25], [50, 25]], ("World", 0.892)],
        [[[100, 30], [125, 30], [125, 45], [100, 45]], ("Test", 0.920)],
    ]

    with patch('screen_vision.ocr.HAS_PADDLE', True):
        with patch('screen_vision.ocr._get_paddle') as mock_get_paddle:
            mock_ocr_instance = MagicMock()
            mock_ocr_instance.ocr.return_value = [mock_paddle_result]
            mock_get_paddle.return_value = mock_ocr_instance

            result = run_ocr(sample_image)

            assert isinstance(result, OcrResult)
            assert "Hello" in result.text
            assert "World" in result.text
            assert "Test" in result.text
            assert len(result.blocks) == 3
            assert result.blocks[0]['text'] == 'Hello'
            assert result.blocks[0]['confidence'] > 90.0
            assert 'bbox' in result.blocks[0]
            # bbox should be (min_x, min_y, max_x, max_y)
            assert result.blocks[0]['bbox'] == (10, 10, 40, 25)
            # Average confidence: (95.5 + 89.2 + 92.0) / 3 ≈ 92.2
            assert 90.0 < result.average_confidence < 95.0


def test_run_ocr_dark_mode_preprocessing(sample_image):
    """run_ocr should handle dark mode images via dual-pass."""
    # Create a dark image
    dark_array = np.full((100, 200, 3), 30, dtype=np.uint8)
    dark_image = Image.fromarray(dark_array)

    # Mock PaddleOCR to return different results for original vs inverted
    mock_original_result = [
        [[[10, 10], [40, 10], [40, 25], [10, 25]], ("Dark", 0.6)],
    ]
    mock_inverted_result = [
        [[[10, 10], [40, 10], [40, 25], [10, 25]], ("Dark", 0.95)],
        [[[50, 10], [85, 10], [85, 25], [50, 25]], ("Text", 0.90)],
    ]

    with patch('screen_vision.ocr.HAS_PADDLE', True):
        with patch('screen_vision.ocr._get_paddle') as mock_get_paddle:
            mock_ocr_instance = MagicMock()
            # Return different results on successive calls
            mock_ocr_instance.ocr.side_effect = [[mock_original_result], [mock_inverted_result]]
            mock_get_paddle.return_value = mock_ocr_instance

            result = run_ocr(dark_image)

            # Should merge results, preferring higher confidence
            assert len(result.blocks) >= 1
            # The "Dark" text should use the inverted result (confidence 0.95)
            dark_block = next((b for b in result.blocks if b['text'] == 'Dark'), None)
            assert dark_block is not None
            assert dark_block['confidence'] > 90.0


def test_extract_text_near_cursor():
    """extract_text_near should return text from blocks near the cursor."""
    blocks = [
        {'text': 'Near', 'bbox': (100, 100, 140, 120)},
        {'text': 'Text', 'bbox': (150, 100, 190, 120)},
        {'text': 'Far', 'bbox': (500, 500, 540, 520)},
    ]
    cursor = {'x': 120, 'y': 110}

    result = extract_text_near(blocks, cursor, radius=200)

    assert 'Near' in result
    assert 'Text' in result
    assert 'Far' not in result


def test_extract_text_near_excludes_far():
    """extract_text_near should exclude blocks far from cursor."""
    blocks = [
        {'text': 'Close', 'bbox': (100, 100, 140, 120)},
        {'text': 'VeryFar', 'bbox': (1000, 1000, 1040, 1020)},
    ]
    cursor = {'x': 110, 'y': 110}

    result = extract_text_near(blocks, cursor, radius=50)

    assert 'Close' in result
    assert 'VeryFar' not in result


def test_ocr_raises_when_no_engine(sample_image):
    """run_ocr should raise NoOcrEngineError when both engines are missing."""
    from screen_vision.ocr import NoOcrEngineError

    with patch('screen_vision.ocr.HAS_PADDLE', False):
        with patch('screen_vision.ocr.HAS_TESSERACT', False):
            with pytest.raises(NoOcrEngineError, match="No OCR engine available"):
                run_ocr(sample_image)


def test_ocr_fallback_to_pytesseract(sample_image):
    """run_ocr should fallback to pytesseract when PaddleOCR is not available."""
    mock_data = {
        'text': ['Hello', 'World'],
        'conf': [95.5, 89.2],
        'left': [10, 50],
        'top': [10, 10],
        'width': [30, 35],
        'height': [15, 15]
    }

    with patch('screen_vision.ocr.HAS_PADDLE', False):
        with patch('screen_vision.ocr.HAS_TESSERACT', True):
            with patch('screen_vision.ocr.pytesseract') as mock_pytesseract:
                mock_pytesseract.image_to_data.return_value = mock_data
                mock_pytesseract.image_to_string.return_value = "Hello World"

                result = run_ocr(sample_image)

                assert isinstance(result, OcrResult)
                assert result.text == "Hello World"
                assert len(result.blocks) == 2


def test_preprocess_detects_dark_background():
    """Dark image preprocessing should invert colors."""
    from screen_vision.ocr import _preprocess_for_ocr

    # Create a dark image (mean < 128)
    dark_array = np.full((100, 200, 3), 30, dtype=np.uint8)
    dark_image = Image.fromarray(dark_array)

    preprocessed = _preprocess_for_ocr(dark_image)

    # After inversion and CLAHE, the image should be brighter
    preprocessed_array = np.array(preprocessed)
    original_mean = np.mean(np.array(dark_image.convert('L')))
    preprocessed_mean = np.mean(preprocessed_array)

    # The preprocessed image should be significantly brighter
    assert preprocessed_mean > original_mean * 2


def test_preprocess_leaves_light_background():
    """Light image preprocessing should not invert colors."""
    from screen_vision.ocr import _preprocess_for_ocr

    # Create a light image (mean > 128)
    light_array = np.full((100, 200, 3), 230, dtype=np.uint8)
    light_image = Image.fromarray(light_array)

    preprocessed = _preprocess_for_ocr(light_image)

    # The image should remain generally light
    preprocessed_array = np.array(preprocessed)
    preprocessed_mean = np.mean(preprocessed_array)

    # Should still be in the brighter half of the spectrum
    assert preprocessed_mean > 128
