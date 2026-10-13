"""Runs the REAL flirimageextractor + exiftool on actual FLIR files.

Skipped unless THERMAL_TEST_IMAGES points at a folder of FLIR .jpg files, because no
survey photos are bundled with the repo.
    THERMAL_TEST_IMAGES=/path/to/flir pytest tests/test_real_flir.py
"""
import glob
import os
import shutil

import pytest

IMAGES = sorted(glob.glob(os.path.join(os.environ.get("THERMAL_TEST_IMAGES", "/nonexistent"), "*.jpg")))
pytestmark = pytest.mark.skipif(not IMAGES or not shutil.which("exiftool"),
                                reason="needs THERMAL_TEST_IMAGES and exiftool")


def test_real_extraction_gives_sane_temperatures():
    from flir_processor import SimpleFLIRProcessor
    temp, stats = SimpleFLIRProcessor().process_single_image(IMAGES[0], display=False)
    assert temp.ndim == 2 and temp.size > 1000
    assert -100 < stats["min"] <= stats["max"] < 500


def test_real_batch_through_app(tmp_path):
    from app import create_app
    from settings import Settings
    from storage import LocalStorage
    app = create_app(storage=LocalStorage(str(tmp_path)), settings=Settings())
    client = app.test_client()
    files = [(open(p, "rb"), os.path.basename(p)) for p in IMAGES[:2]]
    resp = client.post("/upload", data={"files[]": files}, content_type="multipart/form-data")
    assert resp.status_code == 201, resp.get_data(as_text=True)
