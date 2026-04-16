"""OCR module — PaddleOCR with dark mode preprocessing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Any
from PIL import Image
import numpy as np

# Try to import PaddleOCR
try:
    from paddleocr import PaddleOCR
    HAS_PADDLE = True
except ImportError:
    PaddleOCR = None
    HAS_PADDLE = False

_paddle_ocr: Any = None


def _get_paddle():
    """Get singleton PaddleOCR instance."""
    global _paddle_ocr
    if _paddle_ocr is None and HAS_PADDLE:
        _paddle_ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
    return _paddle_ocr

# Fallback to pytesseract
try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    pytesseract = None
    HAS_TESSERACT = False


class NoOcrEngineError(RuntimeError):
    """Raised when no OCR engine (PaddleOCR or pytesseract) is available."""


@dataclass
class OcrResult:
    """Result of OCR operation."""
    text: str
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    average_confidence: float = 0.0


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """
    Preprocess image for better OCR results.

    Steps:
    1. Convert to grayscale
    2. Detect if dark background (mean pixel value < 128)
    3. If dark: invert colors
    4. Apply CLAHE for contrast enhancement
    5. Upscale 2x if image is small (< 600px wide)

    Args:
        image: PIL Image to preprocess

    Returns:
        Preprocessed PIL Image in grayscale
    """
    # Convert to grayscale
    gray = image.convert('L')
    gray_array = np.array(gray)

    # Detect dark background
    mean_value = np.mean(gray_array)
    if mean_value < 128:
        # Invert for dark backgrounds
        gray_array = 255 - gray_array

    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    try:
        import cv2
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray_array)
    except ImportError:
        # If opencv not available, skip CLAHE
        enhanced = gray_array

    # Convert back to PIL Image
    result = Image.fromarray(enhanced)

    # Upscale if small
    if result.width < 600:
        new_size = (result.width * 2, result.height * 2)
        result = result.resize(new_size, Image.LANCZOS)

    return result


def _parse_paddle_result(paddle_result: list) -> tuple[List[Dict[str, Any]], float]:
    """
    Parse PaddleOCR result into blocks and average confidence.

    PaddleOCR returns: [[[box_points], (text, confidence)], ...]
    where box_points is 4 corners [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]

    Args:
        paddle_result: Raw result from PaddleOCR

    Returns:
        Tuple of (blocks, average_confidence)
    """
    blocks = []
    confidences = []

    if not paddle_result or not paddle_result[0]:
        return blocks, 0.0

    for line in paddle_result[0]:
        if not line or len(line) < 2:
            continue

        box_points, (text, confidence) = line

        # Skip empty text
        if not text or not text.strip():
            continue

        # Calculate bounding box from points
        xs = [point[0] for point in box_points]
        ys = [point[1] for point in box_points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        block = {
            'text': text,
            'bbox': (int(min_x), int(min_y), int(max_x), int(max_y)),
            'confidence': confidence * 100,  # Normalize to 0-100
        }
        blocks.append(block)
        confidences.append(confidence * 100)

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return blocks, avg_conf


def _calculate_iou(bbox1: tuple, bbox2: tuple) -> float:
    """
    Calculate Intersection over Union (IoU) between two bounding boxes.

    Args:
        bbox1: (min_x, min_y, max_x, max_y)
        bbox2: (min_x, min_y, max_x, max_y)

    Returns:
        IoU score between 0 and 1
    """
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2

    # Calculate intersection
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)

    if inter_x_max < inter_x_min or inter_y_max < inter_y_min:
        return 0.0

    inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)

    # Calculate union
    area1 = (x1_max - x1_min) * (y1_max - y1_min)
    area2 = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = area1 + area2 - inter_area

    if union_area == 0:
        return 0.0

    return inter_area / union_area


def _merge_blocks(blocks1: List[Dict[str, Any]], blocks2: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge blocks from two OCR passes, deduplicating overlapping blocks.

    When blocks overlap significantly (IoU > 0.5), keep the one with higher confidence.

    Args:
        blocks1: Blocks from first pass
        blocks2: Blocks from second pass

    Returns:
        Merged list of blocks
    """
    merged = list(blocks1)  # Start with first set

    for block2 in blocks2:
        # Check if this block overlaps with any existing block
        is_duplicate = False
        for i, block1 in enumerate(merged):
            iou = _calculate_iou(block1['bbox'], block2['bbox'])
            if iou > 0.5:
                # Overlapping blocks - keep the one with higher confidence
                is_duplicate = True
                if block2['confidence'] > block1['confidence']:
                    merged[i] = block2
                break

        if not is_duplicate:
            merged.append(block2)

    return merged


def _run_paddle_ocr(image: Image.Image) -> OcrResult:
    """
    Run PaddleOCR with dual-pass (original + inverted) for dark mode handling.

    Args:
        image: PIL Image to perform OCR on

    Returns:
        OcrResult with extracted text, blocks, and confidence scores
    """
    try:
        paddle = _get_paddle()

        # Convert PIL Image to numpy array
        image_array = np.array(image)

        # First pass: original image
        result1 = paddle.ocr(image_array, cls=True)
        blocks1, conf1 = _parse_paddle_result(result1)

        # Second pass: inverted image (handles dark mode)
        inverted_array = 255 - image_array
        result2 = paddle.ocr(inverted_array, cls=True)
        blocks2, conf2 = _parse_paddle_result(result2)

        # Merge results, preferring higher confidence
        merged_blocks = _merge_blocks(blocks1, blocks2)

        # Extract text and calculate average confidence
        if merged_blocks:
            text = ' '.join(block['text'] for block in merged_blocks)
            confidences = [block['confidence'] for block in merged_blocks]
            avg_conf = sum(confidences) / len(confidences)
        else:
            text = ""
            avg_conf = 0.0

        return OcrResult(text=text, blocks=merged_blocks, average_confidence=avg_conf)

    except Exception:
        # If OCR fails for any reason, return empty result
        return OcrResult(text="", blocks=[], average_confidence=0.0)


def _run_pytesseract_ocr(image: Image.Image) -> OcrResult:
    """
    Fallback OCR using pytesseract.

    Args:
        image: PIL Image to perform OCR on

    Returns:
        OcrResult with extracted text, blocks, and confidence scores
    """
    if pytesseract is None:
        return OcrResult(text="", blocks=[], average_confidence=0.0)

    try:
        # Get full text
        text = pytesseract.image_to_string(image)

        # Get detailed data with bounding boxes
        data = pytesseract.image_to_data(image, output_type='dict')

        # Build blocks list with filtering
        blocks = []
        confidences = []

        for i in range(len(data['text'])):
            text_item = data['text'][i]
            conf = data['conf'][i]

            # Skip empty text and invalid confidence
            if not text_item.strip() or conf == -1:
                continue

            # Convert pytesseract bbox format to our format
            x, y = data['left'][i], data['top'][i]
            w, h = data['width'][i], data['height'][i]

            block = {
                'text': text_item,
                'bbox': (x, y, x + w, y + h),
                'confidence': float(conf)
            }
            blocks.append(block)
            confidences.append(float(conf))

        # Calculate average confidence
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OcrResult(text=text, blocks=blocks, average_confidence=avg_conf)

    except Exception:
        # If OCR fails for any reason, return empty result
        return OcrResult(text="", blocks=[], average_confidence=0.0)


def run_ocr(image: Image.Image) -> OcrResult:
    """
    Run OCR on an image using PaddleOCR (with pytesseract fallback).

    Uses dual-pass approach for dark mode handling:
    1. Run OCR on original image
    2. Run OCR on inverted image
    3. Merge unique results, preferring higher confidence

    Args:
        image: PIL Image to perform OCR on

    Returns:
        OcrResult with extracted text, blocks, and confidence scores
    """
    # Try PaddleOCR first
    if HAS_PADDLE:
        return _run_paddle_ocr(image)

    # Fall back to pytesseract
    if HAS_TESSERACT:
        return _run_pytesseract_ocr(image)

    # No OCR engine available — fail loudly instead of returning empty results
    raise NoOcrEngineError(
        "No OCR engine available. Install one:\n"
        "  pip install 'screen-vision[ocr]'   (pytesseract + system tesseract binary)\n"
        "    macOS:   brew install tesseract\n"
        "    Linux:   apt-get install tesseract-ocr\n"
        "    Windows: https://github.com/UB-Mannheim/tesseract/wiki\n"
        "  pip install 'screen-vision[paddle]' (PaddleOCR, ~1GB, self-contained)"
    )


def extract_text_near(blocks: List[Dict[str, Any]], cursor: Dict[str, int], radius: int = 200) -> str:
    """
    Extract text from OCR blocks near a cursor position.

    Args:
        blocks: List of OCR blocks with 'text' and 'bbox' keys
        cursor: Dict with 'x' and 'y' keys for cursor position
        radius: Maximum distance in pixels from cursor (default 200)

    Returns:
        Joined text from nearby blocks
    """
    nearby_texts = []
    cursor_x = cursor['x']
    cursor_y = cursor['y']

    for block in blocks:
        bbox = block['bbox']
        # Calculate center of the block
        # bbox is now (min_x, min_y, max_x, max_y)
        block_center_x = (bbox[0] + bbox[2]) / 2
        block_center_y = (bbox[1] + bbox[3]) / 2

        # Calculate Euclidean distance from cursor to block center
        distance = math.sqrt(
            (block_center_x - cursor_x) ** 2 +
            (block_center_y - cursor_y) ** 2
        )

        if distance <= radius:
            nearby_texts.append(block['text'])

    return ' '.join(nearby_texts)
