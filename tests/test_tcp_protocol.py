from types import SimpleNamespace
from unittest.mock import Mock

import asyncio

from src.peer.message import Message, MessageType
from src.peer.tcp_protocol import MAX_MESSAGE_SIZE, TCPProtocol


class FakeLoop:
    def __init__(self, recv_chunks=None) -> None:
        self.recv_chunks = list(recv_chunks or [])
        self.sent = []
        self.connected_to = None

    async def sock_sendall(self, sock, data):
        self.sent.append(data)

    async def sock_connect(self, sock, addr):
        self.connected_to = addr

    async def sock_recv(self, sock, size):
        if self.recv_chunks:
            return self.recv_chunks.pop(0)
        return b""


def _make_protocol(monkeypatch, recv_chunks=None):
    fake_socket = SimpleNamespace(close=Mock())
    monkeypatch.setattr(TCPProtocol, "_create_socket", lambda self: fake_socket)
    protocol = TCPProtocol(
        SimpleNamespace(
            ip="127.0.0.1", port=6881, handshake=lambda **kwargs: b"handshake"
        )
    )
    fake_loop = FakeLoop(recv_chunks=recv_chunks)
    monkeypatch.setattr(
        "src.peer.tcp_protocol.asyncio.get_running_loop", lambda: fake_loop
    )
    return protocol, fake_loop, fake_socket


def test_send_message_and_keepalive(monkeypatch) -> None:
    protocol, fake_loop, _ = _make_protocol(monkeypatch)
    protocol.is_connected = True

    asyncio.run(protocol.send_message(Message(1, MessageType.INTERESTED, b"")))
    asyncio.run(protocol.send_keepalive())

    assert fake_loop.sent[0] == b"\x00\x00\x00\x01\x02"
    assert fake_loop.sent[1] == b"\x00\x00\x00\x00"


def test_wait_for_handshake_and_create_connection(monkeypatch) -> None:
    info_hash = b"i" * 20
    peer_id = b"p" * 20
    handshake = b"\x13BitTorrent protocol" + b"\x00" * 8 + info_hash + peer_id
    protocol, _, _ = _make_protocol(monkeypatch, recv_chunks=[handshake])

    assert asyncio.run(protocol._wait_for_handshake(info_hash)) is True

    protocol, fake_loop, _ = _make_protocol(monkeypatch, recv_chunks=[handshake])
    asyncio.run(protocol.create_connection("127.0.0.1", 6881, info_hash, peer_id))
    assert protocol.is_connected is True
    assert fake_loop.connected_to == ("127.0.0.1", 6881)


def test_receive_message_parses_keepalive_and_payload(monkeypatch) -> None:
    payload = (1).to_bytes(4, "big")
    protocol, _, fake_socket = _make_protocol(
        monkeypatch,
        recv_chunks=[
            b"\x00\x00\x00\x00",
            b"\x00\x00\x00\x05",
            bytes([MessageType.HAVE.value]),
            payload,
        ],
    )

    assert asyncio.run(protocol.receive_message()) is None
    message = asyncio.run(protocol.receive_message())
    assert message is not None
    assert message.msg_type == MessageType.HAVE
    assert message.payload == payload


def test_receive_message_closes_on_large_message_and_wait_for_handshake_failure(
    monkeypatch,
) -> None:
    protocol, _, fake_socket = _make_protocol(
        monkeypatch, recv_chunks=[(MAX_MESSAGE_SIZE + 1).to_bytes(4, "big")]
    )
    assert asyncio.run(protocol.receive_message()) is None
    fake_socket.close.assert_called()

    truncated_handshake = b"\x13BitTorrent protocol" + b"\x00" * 8 + b"i" * 10
    protocol, _, fake_socket = _make_protocol(
        monkeypatch, recv_chunks=[truncated_handshake]
    )
    assert asyncio.run(protocol._wait_for_handshake(b"i" * 20)) is False
    fake_socket.close.assert_not_called()
