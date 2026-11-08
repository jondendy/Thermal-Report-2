"""The drive: a per-group staging folder with 'Upload to drive' and 'Select from drive'."""
import io
import json
import os
import time

import pytest

import cleanup_inbox
from app import create_app
from inbox import Inbox, InboxError
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg


class Down:
    configured = True
    def status(self):
        return {"up": False}


def make(tmp_path, drive=True, **kw):
    settings = Settings(batch_size=3, inbox_root=str(tmp_path / "nas") if drive else "", **kw)
    if drive:
        os.makedirs(settings.inbox_root, exist_ok=True)
    app = create_app(storage=LocalStorage(str(tmp_path / "store")), settings=settings,
                     processor_factory=FakeProcessor)
    app.config["TESTING"] = True
    return app.test_client(), settings


def put(client, *names, stamps=None):
    data = {"files[]": [(io.BytesIO(synthetic_jpeg()), n) for n in names]}
    if stamps:
        data["modified"] = json.dumps(stamps)
    return client.post("/drive/upload", data=data, content_type="multipart/form-data")


# ------------------------------------------------------------------ switched off by default
def test_off_by_default(tmp_path):
    c, _ = make(tmp_path, drive=False)
    page = c.get("/").get_data(as_text=True)
    assert "Upload to drive" not in page and "Select from drive" not in page
    assert c.get("/drive/status").get_json() == {"enabled": False, "available": False, "wake_configured": False}
    assert c.get("/drive/list").status_code == 404
    assert put(c, "a.jpg").status_code == 404


def test_buttons_shown_when_on(tmp_path):
    c, _ = make(tmp_path)
    page = c.get("/").get_data(as_text=True)
    assert "Upload to drive" in page and "Select from drive" in page
    assert c.get("/drive/status").get_json()["available"] is True


# ------------------------------------------------------------------ saving and listing
def test_upload_list_thumb_and_dedupe(tmp_path):
    c, s = make(tmp_path)
    r = put(c, "one.jpg", "two.JPG", stamps=[1700000000000, 1700000100000])
    assert r.status_code == 201 and r.get_json()["saved"] == ["one.jpg", "two.JPG"]
    assert put(c, "one.jpg").get_json()["saved"] == ["one-1.jpg"]          # never overwrites
    files = c.get("/drive/list").get_json()["files"]
    assert [f["name"] for f in files][:2] == ["two.JPG", "one.jpg"] or files[0]["modified"] >= files[1]["modified"]
    assert {"one.jpg", "two.JPG", "one-1.jpg"} == {f["name"] for f in files}
    assert next(f for f in files if f["name"] == "one.jpg")["modified"] == 1700000000
    t = c.get("/drive/thumb/one.jpg")
    assert t.status_code == 200 and t.mimetype == "image/jpeg" and "max-age" in t.headers["Cache-Control"]
    assert c.get("/drive/thumb/nothere.jpg").status_code == 404


def test_rejects_bad_files(tmp_path):
    c, s = make(tmp_path)
    r = c.post("/drive/upload", content_type="multipart/form-data", data={"files[]": [
        (io.BytesIO(b"hello"), "notes.txt"), (io.BytesIO(b"not a picture"), "fake.jpg"),
        (io.BytesIO(synthetic_jpeg()), "../../evil.jpg")]})
    body = r.get_json()
    assert body["saved"] == ["evil.jpg"]                      # path stripped, stays in the folder
    assert {x["name"] for x in body["rejected"]} == {"notes.txt", "fake.jpg"}
    assert not os.path.exists(tmp_path / "evil.jpg")
    assert put(c, "x.txt").status_code == 400


def test_no_traversal_or_delete_outside(tmp_path):
    c, s = make(tmp_path)
    (tmp_path / "secret.jpg").write_bytes(synthetic_jpeg())
    assert c.get("/drive/thumb/..%2Fsecret.jpg").status_code == 404
    c.post("/drive/delete", json={"names": ["../secret.jpg", "/etc/passwd"]})
    assert (tmp_path / "secret.jpg").exists()
    box = Inbox(s.inbox_root, "default")
    for bad in (".jpg", "x" * 200 + ".jpg", "pic.png", ""):
        with pytest.raises(InboxError):
            box._name(bad)
    assert box._name("../secret.jpg") == "secret.jpg"          # directory part dropped, stays in the folder


def test_max_files(tmp_path):
    box = Inbox(str(tmp_path), "g", max_files=2)
    assert box.available()
    box.save("a.jpg", synthetic_jpeg()); box.save("b.jpg", synthetic_jpeg())
    with pytest.raises(InboxError):
        box.save("c.jpg", synthetic_jpeg())


def test_delete(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", "b.jpg")
    assert c.post("/drive/delete", json={"names": ["a.jpg", "zzz.jpg"]}).get_json() == {"deleted": ["a.jpg"]}
    assert [f["name"] for f in c.get("/drive/list").get_json()["files"]] == ["b.jpg"]


# ------------------------------------------------------------------ processing from the drive
def test_process_from_drive(tmp_path):
    c, s = make(tmp_path)
    put(c, "a.jpg", "b.jpg")
    r = c.post("/upload", data={"drive_files": ["a.jpg", "b.jpg"], "sensitivity": "medium"})
    assert r.status_code == 201 and r.get_json()["results"]["summary"]["total_images"] == 2
    assert len(c.get("/drive/list").get_json()["files"]) == 2          # kept by default


def test_delete_after_process_option(tmp_path):
    c, _ = make(tmp_path, inbox_delete_after=True)
    put(c, "a.jpg", "b.jpg")
    assert c.post("/upload", data={"drive_files": ["a.jpg"]}).status_code == 201
    assert [f["name"] for f in c.get("/drive/list").get_json()["files"]] == ["b.jpg"]


def test_mixing_and_limits(tmp_path):
    c, _ = make(tmp_path)
    put(c, "a.jpg", "b.jpg", "c.jpg", "d.jpg")
    mixed = c.post("/upload", content_type="multipart/form-data",
                   data={"drive_files": ["a.jpg"], "files[]": [(io.BytesIO(synthetic_jpeg()), "x.jpg")]})
    assert mixed.status_code == 400
    over = c.post("/upload", data={"drive_files": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]})
    assert over.status_code == 400 and "Maximum 3" in over.get_json()["error"]
    assert c.post("/upload", data={"drive_files": ["gone.jpg"]}).status_code == 400


def test_computer_upload_still_works_with_drive_on(tmp_path):
    c, _ = make(tmp_path)
    r = c.post("/upload", content_type="multipart/form-data",
               data={"files[]": [(io.BytesIO(synthetic_jpeg()), "x.jpg")]})
    assert r.status_code == 201


def test_drive_not_available_blocks_processing(tmp_path):
    c, s = make(tmp_path)
    put(c, "a.jpg")
    os.rename(s.inbox_root, s.inbox_root + "_gone")
    assert c.post("/upload", data={"drive_files": ["a.jpg"]}).status_code == 503
    assert c.get("/drive/list").status_code == 503 and put(c, "z.jpg").status_code == 503


# ------------------------------------------------------------------ availability
def test_marker_required(tmp_path):
    assert not Inbox(str(tmp_path), "g", marker=".mounted").available()
    (tmp_path / ".mounted").write_text("")
    assert Inbox(str(tmp_path), "g", marker=".mounted").available()


def test_asleep_drive_is_not_touched(tmp_path):
    box = Inbox(str(tmp_path), "g", waker=Down())
    assert not box.available() and not (tmp_path / "g").exists()


def test_slow_drive_cannot_hang(tmp_path, monkeypatch):
    box = Inbox(str(tmp_path), "g", wait=0.2)
    monkeypatch.setattr(os.path, "isdir", lambda p: time.sleep(2) or True)
    t = time.time()
    assert box.available() is False and time.time() - t < 1


# ------------------------------------------------------------------ groups and audit
def test_groups_are_separate(tmp_path):
    a, b = Inbox(str(tmp_path), "chesham"), Inbox(str(tmp_path), "amersham")
    assert a.available() and b.available()
    a.save("p.jpg", synthetic_jpeg())
    assert [f["name"] for f in a.list()] == ["p.jpg"] and b.list() == []
    with pytest.raises(InboxError):
        b.read("p.jpg")


def test_audit_entries(tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    orgs = tmp_path / "orgs.json"
    orgs.write_text(json.dumps({"admins": ["boss@example.org"],
                                "organisations": {"chesham": {"name": "Chesham", "members": ["boss@example.org"]}}}))
    s = Settings(orgs_file=str(orgs), auth_mode="dev", dev_user_email="boss@example.org", secret_key="x",
                 inbox_root=str(root))
    app = create_app(storage=LocalStorage(str(tmp_path / "store")), settings=s, processor_factory=FakeProcessor)
    c = app.test_client()
    put(c, "a.jpg")
    assert (root / "chesham" / "a.jpg").exists()
    c.post("/upload", data={"drive_files": ["a.jpg"]})
    log = c.get("/activity").get_data(as_text=True)
    assert "drive_upload" in log or "drive upload" in log.lower()


# ------------------------------------------------------------------ clean-up script
def test_cleanup_script(tmp_path):
    box = Inbox(str(tmp_path), "g"); box.available()
    box.save("old.jpg", synthetic_jpeg(), modified=time.time() - 40 * 86400)
    box.save("new.jpg", synthetic_jpeg())
    assert cleanup_inbox.run(str(tmp_path), 30, dry_run=True) == 1 and len(box.list()) == 2
    assert cleanup_inbox.run(str(tmp_path), 30) == 1
    assert [f["name"] for f in box.list()] == ["new.jpg"]
    assert cleanup_inbox.run(str(tmp_path / "nowhere"), 30) == 0
