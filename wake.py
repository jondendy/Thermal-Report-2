"""Wake a sleeping NAS (e.g. a Synology) with Wake-on-LAN and find out when it is back.

Everything here is off until WAKE_MAC and NAS_HOST are set (see env.example), so the app behaves
exactly as before. Wake-on-LAN only works between machines on the same network.
"""
from __future__ import annotations

import re
import socket
import time
from typing import Optional

_MAC = re.compile(r"^[0-9a-fA-F]{2}([:-]?[0-9a-fA-F]{2}){5}$")


def mac_bytes(mac: str) -> bytes:
    mac = (mac or "").strip()
    if not _MAC.match(mac):
        raise ValueError(f"Not a MAC address: {mac!r} (expected something like AA:BB:CC:DD:EE:FF)")
    return bytes.fromhex(re.sub(r"[:-]", "", mac))


def magic_packet(mac: str) -> bytes:
    """6 bytes of 0xFF followed by the MAC address repeated 16 times."""
    return b"\xff" * 6 + mac_bytes(mac) * 16


def send_wake(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (broadcast or "255.255.255.255", port or 9))


def is_up(host: str, port: int = 445, timeout: float = 2.0) -> bool:
    """True when the NAS accepts a connection on its file-sharing port (445 = SMB)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_until_up(host: str, port: int = 445, seconds: int = 180, interval: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if is_up(host, port):
            return True
        time.sleep(interval)
    return is_up(host, port)


class Waker:
    """Settings-driven wrapper used by the app. ``configured`` is False until a MAC and host are set."""

    MIN_GAP = 30.0                     # seconds between wake packets, so a click-happy user cannot flood the network

    def __init__(self, mac: str = "", host: str = "", port: int = 445, broadcast: str = "",
                 marker: str = "", sender=send_wake, probe=is_up, clock=time.monotonic):
        self.mac, self.host, self.port = mac.strip(), host.strip(), port
        self.broadcast, self.marker = broadcast.strip(), marker.strip()
        self._send, self._probe, self._clock = sender, probe, clock
        self._last: Optional[float] = None
        if self.mac:
            mac_bytes(self.mac)        # fail at start-up on a typo, not on the night

    @property
    def configured(self) -> bool:
        return bool(self.mac and self.host)

    def status(self) -> dict:
        if not self.configured:
            return {"configured": False, "up": True}
        return {"configured": True, "up": bool(self._probe(self.host, self.port))}

    def wake(self) -> dict:
        """Send the wake packet unless one was sent very recently or the NAS is already up."""
        if not self.configured:
            return {"configured": False, "sent": False, "up": True}
        if self._probe(self.host, self.port):
            return {"configured": True, "sent": False, "up": True}
        now = self._clock()
        if self._last is not None and now - self._last < self.MIN_GAP:
            return {"configured": True, "sent": False, "up": False, "waiting": True}
        self._send(self.mac, self.broadcast or "255.255.255.255")
        self._last = now
        return {"configured": True, "sent": True, "up": False}
