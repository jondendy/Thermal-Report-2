from app import create_app
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg
import io


def make(tmp_path, **kw):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(**kw), processor_factory=FakeProcessor)
    return app.test_client()


def test_no_banner_by_default(tmp_path):
    assert "test-banner" not in make(tmp_path).get("/").get_data(as_text=True)


def test_banner_on_pages_but_not_on_the_report(tmp_path):
    c = make(tmp_path, banner_text="TEST SITE <b>")
    assert "TEST SITE &lt;b&gt;" in c.get("/").get_data(as_text=True)          # escaped
    r = c.post("/upload", data={"files[]": [(io.BytesIO(synthetic_jpeg()), "a.jpg")]}, content_type="multipart/form-data")
    bid = r.get_json()["batch_id"]
    assert "test-banner" in c.get(f"/edit_spots/{bid}").get_data(as_text=True)
    assert "test-banner" not in c.get(f"/view_heat_loss_report/{bid}").get_data(as_text=True)
