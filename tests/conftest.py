"""Shared fixtures. No survey photos are bundled with the repo (they can be proprietary):
tests use synthetic images and a stand-in FLIR processor. Set THERMAL_TEST_IMAGES to a
folder of real FLIR .jpg files to also run tests/test_real_flir.py."""
import io
import threading

import numpy as np
import pytest
from PIL import Image

from storage import LocalStorage


class FakeProcessor:
    """Mimics SimpleFLIRProcessor: 120x160 'thermal' array with one obvious hot blob."""

    def process_single_image(self, image_path, display=False):
        rng = np.random.default_rng(42)
        temp = 8.0 + rng.normal(0, 0.3, (120, 160))
        temp[40:50, 60:72] = 27.0              # hot patch, centre ~ (45, 66)
        temp[0:10, 0:10] = -60.0               # "sky reflection" area, must be ignored
        stats = {"min": float(temp.min()), "max": float(temp.max()), "mean": float(temp.mean()),
                 "median": float(np.median(temp)), "std": float(temp.std())}
        return temp, stats


def synthetic_jpeg(width=640, height=480) -> bytes:
    img = Image.new("RGB", (width, height), (40, 40, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def jpeg_bytes():
    return synthetic_jpeg()


@pytest.fixture
def local_storage(tmp_path):
    return LocalStorage(str(tmp_path / "store"))


@pytest.fixture
def webdav_storage(tmp_path):
    """A real WebDAV server (wsgidav + cheroot) on localhost, backed by a temp folder."""
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    from storage.webdav import WebDAVStorage

    root = tmp_path / "dav"
    root.mkdir()
    app = WsgiDAVApp({
        "provider_mapping": {"/": str(root)},
        "simple_dc": {"user_mapping": {"*": True}},     # anonymous access for the test
        "verbose": 0, "logging": {"enable": False},
    })
    server = wsgi.Server(("127.0.0.1", 0), app)
    server.prepare()
    port = server.bind_addr[1]
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        yield WebDAVStorage(f"http://127.0.0.1:{port}")
    finally:
        server.stop()


@pytest.fixture(params=["local", "webdav"])
def any_storage(request, local_storage, webdav_storage):
    return local_storage if request.param == "local" else webdav_storage


@pytest.fixture
def client(any_storage):
    from app import create_app
    from settings import Settings
    app = create_app(storage=any_storage, settings=Settings(batch_size=8),
                     processor_factory=FakeProcessor)
    app.config["TESTING"] = True
    return app.test_client()
