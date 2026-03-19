"""Shared test fixtures for Screen Vision."""
import pytest
from PIL import Image
import numpy as np


@pytest.fixture
def sample_image():
    """A 200x100 test image."""
    return Image.fromarray(np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8))


@pytest.fixture
def work_env():
    return {"SCREEN_VISION_MODE": "work"}


@pytest.fixture
def personal_env():
    return {"SCREEN_VISION_MODE": "personal"}
