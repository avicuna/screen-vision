"""OCR functionality for extracting text from screen captures."""
from dataclasses import dataclass, field
from typing import List, Dict, Any
import math
from PIL import Image

try:
    import pytesseract
except ImportError:
    pytesseract = None


@dataclass
class OcrResult:
    """Result of OCR operation."""
    text: str
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    average_confidence: float = 0.0


def run_ocr(image: Image.Image) -> OcrResult:
    """
    Run OCR on an image using pytesseract.

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

        # Get detailed data with bounding boxes (output_type='dict' returns a dictionary)
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

            block = {
                'text': text_item,
                'bbox': {
                    'x': data['left'][i],
                    'y': data['top'][i],
                    'width': data['width'][i],
                    'height': data['height'][i]
                },
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
        block_center_x = bbox['x'] + bbox['width'] / 2
        block_center_y = bbox['y'] + bbox['height'] / 2

        # Calculate Euclidean distance from cursor to block center
        distance = math.sqrt(
            (block_center_x - cursor_x) ** 2 +
            (block_center_y - cursor_y) ** 2
        )

        if distance <= radius:
            nearby_texts.append(block['text'])

    return ' '.join(nearby_texts)
