import socket

import pytest

from wake import Waker, is_up, mac_bytes, magic_packet, send_wake, wait_until_up


def test_magic_packet_is_six_ff_then_the_mac_sixteen_times():
    pkt = magic_packet("AA:bb:CC:dd:EE:ff")
    assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("aabbccddeeff") * 16
    assert mac_bytes("aa-bb-cc-dd-ee-ff") == mac_bytes("aabbccddeeff")


@pytest.mark.parametrize("bad", ["", "AA:BB", "GG:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF:00"])
def test_a_mistyped_mac_is_rejected_at_start_up(bad):
    with pytest.raises(ValueError):
        magic_packet(bad)
    if bad:
        with pytest.raises(ValueError):
            Waker(mac=bad, host="nas")


def test_packet_really_goes_out_over_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0)); rx.settimeout(2)
    send_wake("AA:BB:CC:DD:EE:FF", "127.0.0.1", rx.getsockname()[1])
    data, _ = rx.recvfrom(200); rx.close()
    assert data == magic_packet("AA:BB:CC:DD:EE:FF")


def test_is_up_follows_whether_something_is_listening():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    assert is_up("127.0.0.1", port) and wait_until_up("127.0.0.1", port, seconds=1)
    srv.close()
    assert not is_up("127.0.0.1", port, timeout=0.5)


def test_unconfigured_waker_does_nothing_and_never_blocks_the_app():
    w = Waker()
    assert not w.configured and w.status() == {"configured": False, "up": True}
    assert w.wake() == {"configured": False, "sent": False, "up": True}


def test_wake_sends_once_then_waits_and_skips_when_already_up():
    sent, now, up = [], [100.0], [False]
    w = Waker("AA:BB:CC:DD:EE:FF", "nas.lan", broadcast="192.168.1.255",
              sender=lambda mac, bc: sent.append((mac, bc)), probe=lambda h, p: up[0], clock=lambda: now[0])
    assert w.wake() == {"configured": True, "sent": True, "up": False}
    assert w.wake()["waiting"] is True and len(sent) == 1           # second click within 30 s: no new packet
    now[0] += 31
    assert w.wake()["sent"] is True and len(sent) == 2
    up[0] = True
    assert w.wake() == {"configured": True, "sent": False, "up": True} and len(sent) == 2
    assert sent[0] == ("AA:BB:CC:DD:EE:FF", "192.168.1.255")


def test_routes_exist_and_are_inert_without_settings(client):
    assert client.get("/storage_status").get_json() == {"configured": False, "up": True}
    assert client.post("/wake_storage").get_json()["sent"] is False


def test_settings_read_the_wake_variables():
    from settings import load_settings
    s = load_settings({"WAKE_MAC": " AA:BB:CC:DD:EE:FF ", "NAS_HOST": "192.168.1.50", "NAS_PORT": "x"})
    assert (s.wake_mac, s.nas_host, s.nas_port, s.wake_broadcast) == ("AA:BB:CC:DD:EE:FF", "192.168.1.50", 445, "")
