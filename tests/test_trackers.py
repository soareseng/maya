import struct

import pytest
import requests

from src.encoder.bencoder import Encoder
from src.tracker.http_tracker import HTTPTracker
from src.tracker.udp_tracker import UDPTracker


def test_http_tracker_parse_response_and_announce() -> None:
    tracker = HTTPTracker()
    encoded = Encoder().encode(
        {b"peers": b"abc", b"interval": 7, b"complete": 1, b"incomplete": 2}
    )

    assert tracker._parse_response(encoded) == {
        "peers": b"abc",
        "interval": 7,
        "complete": 1,
        "incomplete": 2,
    }

    class FakeResponse:
        content = encoded

        def raise_for_status(self) -> None:
            return None

    tracker.session.get = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]
    response = tracker.announce("http://tracker", b"i" * 20, b"p" * 20, 6881)
    assert response["interval"] == 7
    assert response["peers"] == b"abc"


def test_http_tracker_wraps_request_errors() -> None:
    tracker = HTTPTracker()

    def raising_get(*args, **kwargs):
        raise requests.RequestException("boom")

    tracker.session.get = raising_get  # type: ignore[assignment]

    with pytest.raises(ConnectionError, match="Error connecting to tracker"):
        tracker.announce("http://tracker", b"i" * 20, b"p" * 20, 6881)


def test_udp_tracker_endpoint_and_packet_building(monkeypatch) -> None:
    tracker = UDPTracker()
    assert tracker._parse_endpoint("udp://tracker.local:80/announce") == (
        "tracker.local",
        80,
    )

    with pytest.raises(ValueError, match="Unsupported UDP tracker scheme"):
        tracker._parse_endpoint("http://tracker.local:80/announce")

    monkeypatch.setattr(tracker, "_transaction_id", lambda: 1234)
    monkeypatch.setattr(
        "src.tracker.udp_tracker.os.urandom", lambda n: b"\x01\x02\x03\x04"
    )

    request, transaction_id = tracker._build_announce_request(
        connection_id=55,
        info_hash=b"i" * 20,
        peer_id=b"p" * 20,
        port=6881,
        uploaded=1,
        downloaded=2,
        left=3,
        numwant=4,
    )

    assert transaction_id == 1234
    assert request[:8] == struct.pack("!Q", 55)
    assert len(request) == struct.calcsize("!QII20s20sQQQIIIIH")


def test_udp_tracker_parse_response_and_announce(monkeypatch) -> None:
    tracker = UDPTracker()
    peers = b"\x7f\x00\x00\x01\x1a\xe1"

    response = struct.pack("!IIIII", tracker.ANNOUNCE_ACTION, 4321, 45, 6, 7) + peers
    assert tracker._parse_announce_response(response, 4321) == {
        "peers": peers,
        "interval": 45,
        "complete": 7,
        "incomplete": 6,
    }

    with pytest.raises(ConnectionError, match="announce response too short"):
        tracker._parse_announce_response(b"short", 1)

    class FakeSocket:
        def __init__(self) -> None:
            self.sent = []
            self.closed = False
            self.timeout = None
            self.responses = []

        def settimeout(self, timeout):
            self.timeout = timeout

        def sendto(self, data, endpoint):
            self.sent.append((data, endpoint))

        def recvfrom(self, size):
            return self.responses.pop(0), ("tracker.local", 80)

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    fake_socket.responses = [
        struct.pack("!IIQ", tracker.CONNECT_ACTION, 11, 99),
        struct.pack("!IIIII", tracker.ANNOUNCE_ACTION, 11, 30, 2, 3) + peers,
    ]

    monkeypatch.setattr(
        "src.tracker.udp_tracker.socket.socket", lambda *args, **kwargs: fake_socket
    )
    monkeypatch.setattr(tracker, "_transaction_id", lambda: 11)
    monkeypatch.setattr(
        "src.tracker.udp_tracker.os.urandom", lambda n: b"\x01\x02\x03\x04"
    )

    result = tracker.announce(
        "udp://tracker.local:80/announce", b"i" * 20, b"p" * 20, 6881
    )

    assert result == {"peers": peers, "interval": 30, "complete": 3, "incomplete": 2}
    assert fake_socket.closed is True
