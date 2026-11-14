"""check_drive.py (why is the drive not showing up?) and the reason the app gives."""
import io
import os
import time

import check_drive as cd
from app import create_app
from inbox import REASONS, Inbox
from settings import Settings
from storage import LocalStorage
from tests.conftest import FakeProcessor, synthetic_jpeg

UP, DOWN = (lambda h, p: True), (lambda h, p: False)


def statuses(steps):
    return [s[0] for s in steps]


def first_problem(steps):
    return next((s for s in steps if s[0] == cd.PROBLEM), None)


def mounts(tmp_path, *lines):
    f = tmp_path / "mounts"
    f.write_text("\n".join(lines) + "\n")
    return str(f)


# ------------------------------------------------------------------ the script
def test_not_switched_on():
    steps = cd.run_checks({})
    assert statuses(steps) == [cd.PROBLEM] and "INBOX_ROOT" in steps[0][1]


def test_pingable_but_file_sharing_closed_stops_there(tmp_path):
    conf = {"INBOX_ROOT": str(tmp_path), "NAS_HOST": "192.168.1.103", "WAKE_MAC": "00:11:32:78:58:00"}
    steps = cd.run_checks(conf, probe=DOWN)
    assert first_problem(steps)[1].startswith("The NAS does not answer for file sharing")
    assert "ping" in first_problem(steps)[2] and steps[-1][0] == cd.PROBLEM          # nothing tested after it


def test_folder_missing(tmp_path):
    steps = cd.run_checks({"INBOX_ROOT": str(tmp_path / "nowhere")}, probe=UP)
    assert "does not exist" in first_problem(steps)[1]


def test_share_not_mounted_is_spotted(tmp_path):
    root = tmp_path / "synology"; root.mkdir()
    conf = {"INBOX_ROOT": str(root), "NAS_MARKER": ".thermal-mounted"}
    steps = cd.run_checks(conf, mounts_file=mounts(tmp_path, "/dev/sda1 / ext4 rw 0 0"))
    assert "own disk, not the NAS" in first_problem(steps)[1]
    assert any("marker file" in s[1] and s[0] == cd.PROBLEM for s in steps)
    assert not (root / "default").exists()                                         # nothing written to the bare folder


def test_on_demand_mount_that_never_connects(tmp_path):
    root = tmp_path / "synology"; root.mkdir()
    steps = cd.run_checks({"INBOX_ROOT": str(root)},
                          mounts_file=mounts(tmp_path, "/dev/sda1 / ext4 rw 0 0", f"systemd-1 {root} autofs rw 0 0"))
    assert "did not mount" in first_problem(steps)[1] and "credentials" in first_problem(steps)[2]


def test_all_good_on_a_mounted_share(tmp_path):
    root = tmp_path / "synology"; root.mkdir(); (root / ".thermal-mounted").write_text("")
    conf = {"INBOX_ROOT": str(root), "NAS_MARKER": ".thermal-mounted", "NAS_HOST": "192.168.1.103",
            "WAKE_MAC": "00:11:32:78:58:00"}
    table = mounts(tmp_path, "/dev/sda1 / ext4 rw 0 0", f"systemd-1 {root} autofs rw 0 0",
                   f"//192.168.1.103/ThermalImageShares {root} cifs rw 0 0")
    steps = cd.run_checks(conf, probe=UP, mounts_file=table)
    assert first_problem(steps) is None and cd.NOTE not in statuses(steps)
    assert any("//192.168.1.103/ThermalImageShares" in s[1] for s in steps)
    assert (root / "default").is_dir() and os.listdir(root / "default") == []       # test file removed again
    out = io.StringIO()
    assert cd.report(steps, out) is True and "everything the app needs is in place" in out.getvalue()


def test_notes_for_tailscale_address_and_mismatched_host(tmp_path):
    root = tmp_path / "synology"; root.mkdir(); (root / ".m").write_text("")
    table = mounts(tmp_path, f"//192.168.1.103/Share {root} cifs rw 0 0")
    steps = cd.run_checks({"INBOX_ROOT": str(root), "NAS_MARKER": ".m", "NAS_HOST": "100.68.93.107",
                           "WAKE_MAC": "00:11:32:78:58:00"}, probe=UP, mounts_file=table)
    notes = " ".join(s[1] for s in steps if s[0] == cd.NOTE)
    assert "Tailscale" in notes and "mounted from 192.168.1.103 but NAS_HOST is 100.68.93.107" in notes
    assert first_problem(steps) is None


def test_no_write_permission(tmp_path):
    root = tmp_path / "synology"; root.mkdir(); (root / ".m").write_text("")
    os.chmod(root, 0o555)
    try:
        if os.access(root, os.W_OK):                    # running as root: permissions do not apply
            return
        steps = cd.run_checks({"INBOX_ROOT": str(root), "NAS_MARKER": ".m"},
                              mounts_file=mounts(tmp_path, f"//nas/Share {root} cifs rw 0 0"))
        assert "cannot save" in first_problem(steps)[1] and "uid" in first_problem(steps)[2]
    finally:
        os.chmod(root, 0o755)


def test_hanging_mount_is_reported_not_waited_for(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda p: time.sleep(3) or True)
    t = time.time()
    steps = cd.run_checks({"INBOX_ROOT": str(tmp_path)}, wait=0.2)
    assert "did not answer" in first_problem(steps)[1] and time.time() - t < 2


def test_read_settings(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SECRET_KEY=x\nINBOX_ROOT=/mnt/synology\n# NAS_HOST=1.2.3.4\nNAS_MARKER='.thermal-mounted'\n")
    got = cd.read_settings(str(env), environ={"NAS_HOST": "192.168.1.103"})
    assert got == {"INBOX_ROOT": "/mnt/synology", "NAS_MARKER": ".thermal-mounted", "NAS_HOST": "192.168.1.103"}


def test_mount_for_picks_the_deepest_and_latest(tmp_path):
    table = mounts(tmp_path, "/dev/sda1 / ext4 rw 0 0", "systemd-1 /mnt/synology autofs rw 0 0",
                   "//nas/Share /mnt/synology cifs rw 0 0", "/dev/sdb1 /mnt/data ext4 rw 0 0")
    assert cd.mount_for("/mnt/synology/chesham", table) == ("//nas/Share", "/mnt/synology", "cifs")
    assert cd.mount_for("/mnt/synology2", table)[2] == "ext4"
    assert cd.mount_for("/mnt", str(tmp_path / "missing")) is None


# ------------------------------------------------------------------ what the app says
class Asleep:
    configured = True
    def status(self):
        return {"up": False}


def test_inbox_reasons(tmp_path):
    assert Inbox(str(tmp_path), "g").check() == "ok"
    assert Inbox(str(tmp_path / "gone"), "g").check() == "no_folder"
    assert Inbox(str(tmp_path), "g", marker=".m").check() == "not_mounted"
    assert Inbox(str(tmp_path), "g", waker=Asleep()).check() == "asleep"
    assert set(REASONS) == {"ok", "asleep", "no_folder", "not_mounted", "no_permission", "slow"}


def test_status_and_errors_carry_the_reason(tmp_path):
    root = tmp_path / "nas"; root.mkdir()
    app = create_app(storage=LocalStorage(str(tmp_path / "s")),
                     settings=Settings(inbox_root=str(root), nas_marker=".thermal-mounted"), processor_factory=FakeProcessor)
    c = app.test_client()
    st = c.get("/drive/status").get_json()
    assert st["available"] is False and st["reason"] == "not_mounted" and "not connected to the server" in st["message"]
    assert str(root) not in st["message"]                                         # no server paths shown to users
    r = c.get("/drive/list")
    assert r.status_code == 503 and r.get_json()["reason"] == "not_mounted"
    r = c.post("/upload", data={"drive_files": ["a.jpg"]})
    assert r.status_code == 503 and "not connected" in r.get_json()["error"]
    (root / ".thermal-mounted").write_text("")
    assert c.get("/drive/status").get_json() == {"enabled": True, "available": True, "reason": "ok", "message": "",
                                                 "wake_configured": False}
