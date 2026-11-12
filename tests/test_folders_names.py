"""Drive folders, batch names, the address pre-fill and the picture date in the notes."""
import io
import json
import os
import time
from datetime import date, datetime

import pytest
from PIL import Image

import cleanup_inbox
from app import create_app
from inbox import Inbox, InboxError
from services.batch_service import picture_date, taken_sentence
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg
from tests.test_app_flow import edit_state


def jpeg_taken(when: str) -> bytes:
    """A JPEG carrying a camera date, e.g. '2026:11:05 19:30:00'."""
    img = Image.new("RGB", (640, 480), (40, 40, 60))
    exif = Image.Exif()
    exif[306] = when
    exif.get_ifd(0x8769)[36867] = when
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def make(tmp_path, **kw):
    settings = Settings(batch_size=3, inbox_root=str(tmp_path / "nas"), **kw)
    os.makedirs(settings.inbox_root, exist_ok=True)
    app = create_app(storage=LocalStorage(str(tmp_path / "store")), settings=settings, processor_factory=FakeProcessor)
    app.config["TESTING"] = True
    return app.test_client(), settings


def put(client, *names, folder=None, data=None):
    form = {"files[]": [(io.BytesIO(data or synthetic_jpeg()), n) for n in names]}
    if folder is not None:
        form["folder"] = folder
    return client.post("/drive/upload", data=form, content_type="multipart/form-data")


# ------------------------------------------------------------------ folders on the drive
def test_upload_into_a_folder_and_browse(tmp_path):
    c, s = make(tmp_path)
    assert put(c, "a.jpg", "b.jpg", folder="12 High Street, Chesham").get_json()["folder"] == "12 High Street, Chesham"
    put(c, "loose.jpg")
    assert os.path.isfile(os.path.join(s.inbox_root, "default", "12 High Street, Chesham", "a.jpg"))
    root = c.get("/drive/list").get_json()
    assert [f["name"] for f in root["files"]] == ["loose.jpg"]
    assert [(f["name"], f["count"]) for f in root["folders"]] == [("12 High Street, Chesham", 2)]
    inside = c.get("/drive/list", query_string={"folder": "12 High Street, Chesham"}).get_json()
    assert {f["name"] for f in inside["files"]} == {"a.jpg", "b.jpg"}
    assert c.get("/drive/thumb/a.jpg", query_string={"folder": "12 High Street, Chesham"}).status_code == 200
    assert c.get("/drive/thumb/a.jpg").status_code == 404                  # not in the main folder


def test_same_name_in_two_folders_is_fine(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", folder="One")
    assert put(c, "a.jpg", folder="Two").get_json()["saved"] == ["a.jpg"]
    assert put(c, "a.jpg", folder="Two").get_json()["saved"] == ["a-1.jpg"]


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", ".hidden", "***", ".."])
def test_bad_folder_names_refused(tmp_path, bad):
    c, s = make(tmp_path)
    assert put(c, "a.jpg", folder=bad).status_code == 400
    assert c.get("/drive/list", query_string={"folder": bad}).status_code == 400
    assert c.post("/upload", data={"drive_files": ["a.jpg"], "drive_folder": bad}).status_code == 400
    assert set(os.listdir(tmp_path)) <= {"nas", "store"}                  # nothing written outside
    assert os.listdir(os.path.join(s.inbox_root, "default")) == []


def test_no_reading_outside_through_a_folder(tmp_path):
    c, s = make(tmp_path)
    put(c, "a.jpg")
    os.makedirs(tmp_path / "nas" / "other-group")
    (tmp_path / "nas" / "other-group" / "secret.jpg").write_bytes(synthetic_jpeg())
    assert c.get("/drive/thumb/secret.jpg", query_string={"folder": "../other-group"}).status_code == 404
    os.symlink(tmp_path / "nas" / "other-group", tmp_path / "nas" / "default" / "link")     # a planted link
    assert c.get("/drive/thumb/secret.jpg", query_string={"folder": "link"}).status_code == 404


def test_delete_in_folder_removes_empty_folder(tmp_path):
    c, s = make(tmp_path)
    put(c, "a.jpg", "b.jpg", folder="House")
    c.post("/drive/delete", json={"names": ["a.jpg"], "folder": "House"})
    assert [f["name"] for f in c.get("/drive/list").get_json()["folders"]] == ["House"]
    c.post("/drive/delete", json={"names": ["b.jpg"], "folder": "House"})
    assert c.get("/drive/list").get_json()["folders"] == []
    assert os.path.isdir(os.path.join(s.inbox_root, "default"))            # the main folder stays


def test_limit_counts_every_folder(tmp_path):
    box = Inbox(str(tmp_path), "g", max_files=2)
    assert box.available()
    box.save("a.jpg", synthetic_jpeg(), folder="One")
    box.save("b.jpg", synthetic_jpeg(), folder="Two")
    with pytest.raises(InboxError):
        box.save("c.jpg", synthetic_jpeg())


def test_cleanup_covers_folders(tmp_path):
    box = Inbox(str(tmp_path), "g"); box.available()
    old = time.time() - 40 * 86400
    box.save("old.jpg", synthetic_jpeg(), modified=old, folder="Old house")
    box.save("new.jpg", synthetic_jpeg(), folder="New house")
    assert cleanup_inbox.run(str(tmp_path), 30, dry_run=True) == 1 and len(box.folders()) == 2
    assert cleanup_inbox.run(str(tmp_path), 30) == 1
    assert [f["name"] for f in box.folders()] == ["New house"]


# ------------------------------------------------------------------ batch name and address
def test_folder_names_the_batch_and_fills_the_address(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", "b.jpg", folder="12 High Street", data=jpeg_taken("2026:11:05 19:30:00"))
    r = c.post("/upload", data={"drive_files": ["a.jpg", "b.jpg"], "drive_folder": "12 High Street"})
    assert r.status_code == 201 and r.get_json()["name"] == "12 High Street"
    bid = r.get_json()["batch_id"]
    home = c.get("/").get_data(as_text=True)
    assert "12 High Street" in home and bid in home
    state = edit_state(c, bid)
    assert state["name"] == "12 High Street" and state["property_address"] == "12 High Street"
    assert state["general_notes"] == "Survey pictures taken: 5 November 2026.\n"
    assert state["images"][0]["spots"][0]["type"] == "Window"          # points still get their default labels


def test_given_name_wins_and_local_upload_has_no_address(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", folder="Folder")
    r = c.post("/upload", data={"drive_files": ["a.jpg"], "drive_folder": "Folder", "batch_name": "Evening round"})
    assert r.get_json()["name"] == "Evening round"
    r = c.post("/upload", content_type="multipart/form-data",
               data={"files[]": [(io.BytesIO(synthetic_jpeg()), "x.jpg")], "batch_name": "  From laptop  "})
    bid = r.get_json()["batch_id"]
    assert r.get_json()["name"] == "From laptop"
    assert edit_state(c, bid)["property_address"] == ""


def test_rename(tmp_path):
    c, _ = make(tmp_path)
    bid = c.post("/upload", content_type="multipart/form-data",
                 data={"files[]": [(io.BytesIO(synthetic_jpeg()), "x.jpg")]}).get_json()["batch_id"]
    assert c.post(f"/rename/{bid}", json={"name": "<b>3 Mill Lane</b>" + "x" * 300}).status_code == 200
    home = c.get("/").get_data(as_text=True)
    assert "&lt;b&gt;3 Mill Lane" in home and "<b>3 Mill Lane" not in home     # shown as text, cut to length
    assert len(c.get("/api/batches").get_json()["batches"][0]["name"]) == 120
    assert c.post(f"/rename/{bid}", json={"name": ""}).get_json()["name"] == ""
    assert c.post("/rename/batch_20200101_000000_deadbeef", json={"name": "x"}).status_code == 404


def test_saving_points_keeps_name_address_and_notes(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", folder="Rose Cottage", data=jpeg_taken("2026:11:05 19:30:00"))
    bid = c.post("/upload", data={"drive_files": ["a.jpg"], "drive_folder": "Rose Cottage"}).get_json()["batch_id"]
    c.post(f"/redetect/{bid}", json={"sensitivity": "high"})
    state = edit_state(c, bid)
    assert state["property_address"] == "Rose Cottage" and state["general_notes"].startswith("Survey pictures taken: 5 November 2026.")
    state["general_notes"] += "Cold, clear night."
    c.post(f"/save_labels/{bid}", json=state)
    state = edit_state(c, bid)
    assert state["general_notes"] == "Survey pictures taken: 5 November 2026.\nCold, clear night."
    assert c.get("/api/batches").get_json()["batches"][0]["name"] == "Rose Cottage"


# ------------------------------------------------------------------ the date pictures were taken
def test_picture_date_sources():
    assert picture_date(jpeg_taken("2026:11:05 19:30:00")) == date(2026, 11, 5)
    assert picture_date(synthetic_jpeg()) is None
    stamp = datetime(2026, 11, 3, 12, 0).timestamp()
    assert picture_date(synthetic_jpeg(), stamp) == date(2026, 11, 3)                 # file date when no camera date
    assert picture_date(jpeg_taken("2026:11:05 19:30:00"), stamp) == date(2026, 11, 5)  # camera date wins
    assert picture_date(jpeg_taken("1970:01:01 00:00:00")) is None                    # camera clock never set
    assert picture_date(jpeg_taken("2099:01:01 00:00:00")) is None
    assert picture_date(b"not a picture") is None
    assert picture_date(jpeg_taken("garbage")) is None


def test_taken_sentence():
    assert taken_sentence([]) == ""
    assert taken_sentence([date(2026, 11, 5), date(2026, 11, 5)]) == "Survey pictures taken: 5 November 2026."
    assert taken_sentence([date(2026, 11, 6), date(2026, 11, 4)]) == \
        "Survey pictures taken: 4 November 2026 to 6 November 2026."


def test_local_upload_uses_camera_date_then_file_date(tmp_path):
    c, _ = make(tmp_path)
    stamp = int(datetime(2026, 10, 30, 20, 0).timestamp() * 1000)
    r = c.post("/upload", content_type="multipart/form-data", data={
        "files[]": [(io.BytesIO(jpeg_taken("2026:11:05 19:30:00")), "a.jpg"), (io.BytesIO(synthetic_jpeg()), "b.jpg")],
        "modified": json.dumps([None, stamp])})
    bid = r.get_json()["batch_id"]
    assert r.get_json()["results"]["pictures_taken"] == ["2026-10-30", "2026-11-05"]
    assert "Survey pictures taken: 30 October 2026 to 5 November 2026." in c.get(f"/edit_spots/{bid}").get_data(as_text=True)


def test_no_date_no_line_and_bad_stamps_ignored(tmp_path):
    c, _ = make(tmp_path)
    r = c.post("/upload", content_type="multipart/form-data",
               data={"files[]": [(io.BytesIO(synthetic_jpeg()), "a.jpg")], "modified": "{not json"})
    bid = r.get_json()["batch_id"]
    assert r.status_code == 201 and r.get_json()["results"]["pictures_taken"] == []
    assert "Survey pictures taken" not in c.get(f"/edit_spots/{bid}").get_data(as_text=True)
    r = c.post("/upload", content_type="multipart/form-data",
               data={"files[]": [(io.BytesIO(synthetic_jpeg()), "a.jpg")], "modified": json.dumps(["x", {"a": 1}])})
    assert r.status_code == 201


def test_date_and_address_reach_the_report(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", folder="Rose Cottage", data=jpeg_taken("2026:11:05 19:30:00"))
    bid = c.post("/upload", data={"drive_files": ["a.jpg"], "drive_folder": "Rose Cottage"}).get_json()["batch_id"]
    state = edit_state(c, bid)
    state["images"][0]["spots"][0].update(type="Wall", spot_number=1)
    assert c.post(f"/save_labels/{bid}", json=state).status_code == 200
    assert c.post(f"/generate_heat_loss_report/{bid}", json={}).status_code == 200
    report = c.get(f"/view_heat_loss_report/{bid}").get_data(as_text=True)
    assert "Rose Cottage" in report and "Survey pictures taken: 5 November 2026." in report
