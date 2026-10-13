"""The same contract is run against the local and the WebDAV backend."""
import pytest

from storage import StorageError, StorageNotFound, clean_path, create_storage


def test_roundtrip_text_json_bytes(any_storage):
    s = any_storage
    s.write_text("a/b/note.txt", "héllo")
    s.write_json("a/b/data.json", {"x": [1, 2, 3]})
    s.write_bytes("a/img.bin", b"\x00\x01\x02")
    assert s.read_text("a/b/note.txt") == "héllo"
    assert s.read_json("a/b/data.json") == {"x": [1, 2, 3]}
    assert s.read_bytes("a/img.bin") == b"\x00\x01\x02"


def test_exists_isdir_listdir(any_storage):
    s = any_storage
    s.write_text("top/one.txt", "1")
    s.write_text("top/sub/two.txt", "2")
    assert s.exists("top") and s.is_dir("top") and not s.is_dir("top/one.txt")
    assert s.exists("top/one.txt") and not s.exists("top/nope.txt")
    assert s.listdir("top") == ["one.txt", "sub"]
    assert s.listdir("does/not/exist") == []


def test_overwrite_and_delete(any_storage):
    s = any_storage
    s.write_text("d/f.txt", "old")
    s.write_text("d/f.txt", "new")
    assert s.read_text("d/f.txt") == "new"
    s.write_text("d/sub/g.txt", "g")
    s.delete("d")
    assert not s.exists("d") and not s.exists("d/f.txt")
    s.delete("d")                       # deleting something absent is not an error


def test_missing_file_raises_not_found(any_storage):
    with pytest.raises(StorageNotFound):
        any_storage.read_bytes("nothing/here.txt")


def test_local_copy_gives_real_file_and_cleans_up(any_storage):
    import os
    any_storage.write_bytes("img/p.jpg", b"JPEGDATA")
    with any_storage.local_copy("img/p.jpg") as path:
        assert path.endswith(".jpg")
        assert open(path, "rb").read() == b"JPEGDATA"
    assert not os.path.exists(path)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../x", "/abs/../..", "a//b", "a/./b"])
def test_path_escape_rejected(local_storage, bad):
    with pytest.raises(StorageError):
        local_storage.write_text(bad, "x")


def test_clean_path_normalises():
    assert clean_path("/a\\b/c/") == "a/b/c"
    assert clean_path("") == ""


def test_factory_selects_backend(tmp_path):
    s = create_storage({"STORAGE_BACKEND": "local", "STORAGE_ROOT": str(tmp_path / "x")})
    assert s.describe().startswith("local:")
    with pytest.raises(StorageError):
        create_storage({"STORAGE_BACKEND": "webdav"})              # URL missing
    with pytest.raises(StorageError):
        create_storage({"STORAGE_BACKEND": "smb", "SMB_SERVER": "h"})   # share missing
    with pytest.raises(StorageError):
        create_storage({"STORAGE_BACKEND": "ftp"})


def test_smb_unc_path_building():
    """No SMB server is available in CI, so only the path logic is checked here."""
    from storage.smb import SMBStorage
    s = object.__new__(SMBStorage)
    s.server, s.share, s.base_dir = "nas", "surveys", "thermal-report"
    assert s._unc("") == r"\\nas\surveys\thermal-report"
    assert s._unc("reports/batches/b1/results.json") == r"\\nas\surveys\thermal-report\reports\batches\b1\results.json"
    with pytest.raises(StorageError):
        s._unc("../escape")
