"""Why is the drive not showing up? Run this on the server, in the app's folder, as the same user
the service runs as:

    venv/bin/python check_drive.py

It walks through everything the app needs before it will use the drive, in the order the app
checks them, and says which step fails and what to do about it. It reads the same settings as the
app (the environment, else the .env file next to this script). It changes nothing, except creating
the group's own folder on the drive if that is missing (the app does the same).

"I can ping it" only proves the NAS is switched on. The app needs four more things: file sharing
answering on the NAS, the share mounted on this server, the marker file on the share, and
permission to write there.
"""
from __future__ import annotations

import getpass
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

from inbox import _within
from wake import is_up

KEYS = ("INBOX_ROOT", "NAS_MARKER", "NAS_HOST", "NAS_PORT", "WAKE_MAC", "WAKE_BROADCAST", "ORGS_FILE")
NETWORK_TYPES = ("cifs", "smb3", "nfs", "nfs4", "fuse.sshfs", "davfs")
OK, PROBLEM, NOTE = "OK", "PROBLEM", "NOTE"
Step = Tuple[str, str, str]                     # (status, what was checked, what to do)


def read_settings(env_path: str, environ=os.environ) -> Dict[str, str]:
    found: Dict[str, str] = {}
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep and key in KEYS:
                    found[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    found.update({k: environ[k] for k in KEYS if environ.get(k)})
    return found


def mount_for(path: str, mounts_file: str = "/proc/mounts") -> Optional[Tuple[str, str, str]]:
    """(source, mount point, type) of the mount that holds this path: the deepest one wins, and for
    an automount the real mount listed after its placeholder."""
    path = os.path.abspath(path)
    best: Optional[Tuple[str, str, str]] = None
    try:
        with open(mounts_file, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                source, point, kind = parts[0], parts[1].replace("\\040", " "), parts[2]
                if path == point or path.startswith(point.rstrip("/") + "/"):
                    if best is None or len(point) >= len(best[1]):
                        best = (source, point, kind)
    except OSError:
        return None
    return best


def run_checks(conf: Dict[str, str], probe: Callable = is_up, mounts_file: str = "/proc/mounts",
               wait: float = 5.0, group: str = "default") -> List[Step]:
    steps: List[Step] = []
    root, marker = conf.get("INBOX_ROOT", "").strip(), conf.get("NAS_MARKER", "").strip()
    host, mac = conf.get("NAS_HOST", "").strip(), conf.get("WAKE_MAC", "").strip()
    try:
        port = int(conf.get("NAS_PORT") or 445)
    except ValueError:
        port = 445

    # 1. is the feature switched on at all?
    if not root:
        return [(PROBLEM, "INBOX_ROOT is not set",
                 "Add INBOX_ROOT=/mnt/synology (the folder the share is mounted on) to .env and restart the "
                 "service. Without it the two drive buttons are not shown at all.")]
    steps.append((OK, f"INBOX_ROOT is set to {root}", ""))

    # 2. is the NAS answering for file sharing? (the app refuses to touch the folder if not)
    if host:
        if host.startswith("100."):
            steps.append((NOTE, f"NAS_HOST is {host}, which looks like a Tailscale address",
                          "Use the NAS's home-network address (192.168.x.x) so the check does not depend on Tailscale."))
        if probe(host, port):
            steps.append((OK, f"The NAS answers for file sharing ({host} port {port})", ""))
        else:
            steps.append((PROBLEM, f"The NAS does not answer for file sharing ({host} port {port})",
                          "It may answer a ping and still fail this. Check NAS_HOST is the NAS's address, that SMB is "
                          "switched on (Synology: Control Panel > File Services > SMB) and that the Synology firewall "
                          "allows this server. "
                          + ("While this fails the app treats the drive as asleep and will not look any further."
                             if mac else "")))
            if mac:
                return steps
    else:
        steps.append((NOTE, "NAS_HOST is not set, so the app does not test whether the NAS is awake",
                      "Fine for a drive that is always on. Set NAS_HOST and WAKE_MAC to have the app wake it."))
    if bool(host) != bool(mac):
        steps.append((NOTE, "Only one of NAS_HOST and WAKE_MAC is set",
                      "Both are needed before the app will wake the NAS or wait for it."))

    # 3. does the folder exist, and is it really the NAS?
    exists = _within(wait, lambda: os.path.isdir(root), default=None)
    if exists is None:
        steps.append((PROBLEM, f"{root} did not answer within {wait:g} seconds",
                      "The mount is hanging. Try: sudo systemctl restart mnt-synology.automount  (the name follows "
                      "the mount point), then run this again. Check the password in the credentials file too."))
        return steps
    if not exists:
        steps.append((PROBLEM, f"The folder {root} does not exist on this server",
                      f"Create the mount point (sudo mkdir -p {root}) and check the line for it in /etc/fstab, or "
                      "correct INBOX_ROOT."))
        return steps
    _within(wait, lambda: os.listdir(root), default=None)      # looking inside makes an on-demand mount connect
    mount = mount_for(root, mounts_file)
    if mount and mount[2] in NETWORK_TYPES:
        steps.append((OK, f"{root} is on the network share {mount[0]} (mounted at {mount[1]})", ""))
        nas_ip = mount[0].lstrip("/").split("/")[0].split(":")[0]
        if host and nas_ip and nas_ip != host:
            steps.append((NOTE, f"The share is mounted from {nas_ip} but NAS_HOST is {host}",
                          "These should be the same machine. If they are not, the app is testing the wrong one."))
    elif mount and mount[2] == "autofs":
        steps.append((PROBLEM, f"{root} is set to mount on demand, but the share did not mount",
                      f"Try it by hand to see the real error: sudo mount {mount[1]}   then: sudo dmesg | tail -5 . "
                      "The usual causes are a wrong password or user in the credentials file, a wrong share name, or "
                      "an SMB version the NAS refuses (try vers=2.1 or vers=3.0 in /etc/fstab)."))
    else:
        steps.append((PROBLEM if marker else NOTE,
                      f"{root} is an ordinary folder on this server's own disk, not the NAS",
                      "The share is not mounted. Check: grep synology /etc/fstab ; sudo systemctl daemon-reload ; "
                      f"sudo mount {root} ; and look at the error it prints. (If you meant to use a local folder for "
                      "testing, this is fine.)"))

    # 4. the marker file: the app's proof that the share is mounted
    if marker:
        there = _within(wait, lambda: os.path.exists(os.path.join(root, marker)), default=False)
        if there:
            steps.append((OK, f"The marker file {marker} is on the share", ""))
        else:
            steps.append((PROBLEM, f"The marker file {os.path.join(root, marker)} is missing",
                          f"With the share mounted, create it once: touch '{os.path.join(root, marker)}' . It must "
                          "sit directly in INBOX_ROOT (not in a sub-folder), with exactly the name in NAS_MARKER. "
                          "Until it exists the app treats the share as not mounted."))
            return steps
    else:
        steps.append((NOTE, "NAS_MARKER is not set",
                      "The app cannot tell a mounted share from an empty mount point. Recommended: "
                      f"touch {root}/.thermal-mounted and set NAS_MARKER=.thermal-mounted"))

    # 5. may this user write there?
    folder = os.path.join(root, group)
    def writable():
        os.makedirs(folder, exist_ok=True)
        probe_file = os.path.join(folder, ".write-test")
        with open(probe_file, "w") as fh:
            fh.write("ok")
        os.remove(probe_file)
        return True
    try:
        can = _within(wait, writable, default=False)
    except Exception:                                   # noqa: BLE001
        can = False
    user = getpass.getuser()
    if can:
        steps.append((OK, f"The user '{user}' can save pictures in {folder}", ""))
    else:
        steps.append((PROBLEM, f"The user '{user}' cannot save in {folder}",
                      "Give that user the share: in /etc/fstab the mount options need uid and gid set to the user the "
                      "service runs as (see User= in the service file), and the NAS user in the credentials file needs "
                      "Read/Write on the shared folder. Then: sudo umount " + root + " ; sudo mount " + root))
    return steps


def report(steps: List[Step], out=sys.stdout) -> bool:
    problems = [s for s in steps if s[0] == PROBLEM]
    for n, (status, what, advice) in enumerate(steps, 1):
        print(f"{n:>2}. [{status:^7}] {what}", file=out)
        if advice:
            print(f"              -> {advice}", file=out)
    print(file=out)
    if problems:
        print(f"Result: the app cannot use the drive. Fix step {steps.index(problems[0]) + 1} first; "
              "later steps were not all tested.", file=out)
    else:
        print("Result: everything the app needs is in place. If the buttons still do not work, restart the "
              "service (it reads .env only when it starts) and make sure you ran this as the service's user.", file=out)
    return not problems


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    settings = read_settings(os.path.join(here, ".env"))
    print(f"Drive check, as user '{getpass.getuser()}', settings from {os.path.join(here, '.env')}\n")
    if settings.get("ORGS_FILE"):
        print("Groups are switched on: each group has its own folder on the drive; this tests the 'default' one.\n")
    sys.exit(0 if report(run_checks(settings)) else 1)
