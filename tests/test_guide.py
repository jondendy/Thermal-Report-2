"""The user guide matches how the site is set up, and the raw-data link is gone from the menus."""
import io
import os

from app import create_app
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg
from tests.test_orgs import as_user, make_app, orgs_file      # noqa: F401  (orgs_file is a fixture)


def guide(tmp_path, headers=None, **kw):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(**kw), processor_factory=FakeProcessor)
    r = app.test_client().get("/info", headers=headers or {})
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_basic_guide_uses_real_settings(tmp_path):
    page = guide(tmp_path, batch_size=6)
    assert "up to 6" in page and "Maximum 6 images per batch" in page
    for text in ("Process Batch", "Save &amp; Generate Report", "Re-detect points", "Apply colours", "Ironbow",
                 "8°C or more", "4 to 8°C", "2 to 4°C", "under 2°C", "Wi = window", "Fewest, Fewer, Standard, More, Most"):
        assert text in page, text
    # parts for features that are switched off are left out
    for text in ("Upload to drive", "Switch group", "Log out", 'href="#drive"', 'href="#signin"'):
        assert text not in page, text
    # nothing left from the old guide that the site does not do
    for text in ("CSV", "blower door", "Dual-Method", "30°C", "feet"):
        assert text not in page, text


def test_drive_section_follows_the_drive_settings(tmp_path):
    os.makedirs(tmp_path / "nas")
    page = guide(tmp_path, inbox_root=str(tmp_path / "nas"), inbox_keep_days=45)
    assert "Upload to drive" in page and "Select from drive" in page
    assert "after about 45 days" in " ".join(page.split())
    assert "stay on the drive after a batch is made" in " ".join(page.split())
    page = guide(tmp_path, inbox_root=str(tmp_path / "nas"), inbox_delete_after=True)
    assert "removed from the drive once a batch has been made" in " ".join(page.split())


def test_sign_in_section_only_behind_the_login(tmp_path):
    page = guide(tmp_path, headers={"Cf-Access-Jwt-Assertion": "x"})
    assert 'id="signin"' in page and "is at the top right" in " ".join(page.split()) and "Switch group" not in page


def test_group_guide(tmp_path, orgs_file):
    c = as_user(make_app(tmp_path, orgs_file), "alice@example.org")
    page = " ".join(c.get("/info").get_data(as_text=True).split())
    assert "for Sustainable Chesham Energy Group" in page and "Switch group" in page
    assert "belonging to the group shown in the bar" in page and "belongs to another group" in page


def test_help_address_still_works(tmp_path):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(), processor_factory=FakeProcessor)
    assert app.test_client().get("/help").headers["Location"].endswith("/info")


def test_no_raw_data_link_in_the_menus(tmp_path):
    app = create_app(storage=LocalStorage(str(tmp_path / "s")), settings=Settings(), processor_factory=FakeProcessor)
    c = app.test_client()
    bid = c.post("/upload", data={"files[]": [(io.BytesIO(synthetic_jpeg()), "a.jpg")]},
                 content_type="multipart/form-data").get_json()["batch_id"]
    for url in ("/", f"/report/{bid}", "/info"):
        page = c.get(url).get_data(as_text=True)
        assert "/api/batches" not in page and ">📊 API<" not in page, url
    assert "User Guide" in c.get("/").get_data(as_text=True)
    assert c.get("/api/batches").status_code == 200           # still there for the pages and tests that use it
