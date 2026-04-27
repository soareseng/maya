from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.peer.message import Message, MessageType
from src.peer.peer import BLOCK_SIZE, Peer


class FakePieceManager:
    def __init__(self) -> None:
        self.downloaded: set[int] = set()
        self.available: set[int] = set()
        self.acquire_piece = AsyncMock(return_value=1)
        self.get_piece_size = Mock(return_value=BLOCK_SIZE * 2 + 16)
        self.register_block = AsyncMock(return_value=False)
        self.mark_piece_available = Mock()
        self.peer_manager = SimpleNamespace(get_peers=lambda: set())


def test_handshake_and_update_bitfield_validation() -> None:
    peer = Peer(peer_id=b"peer", number_of_pieces=16)

    assert (
        peer.handshake(b"a" * 20, b"b" * 20)
        == b"\x13BitTorrent protocol" + b"\x00" * 8 + b"a" * 20 + b"b" * 20
    )
    assert peer.update_bitfield(b"\xff\x00") == b"\xff\x00"

    with pytest.raises(ValueError, match="Invalid bitfield size"):
        peer.update_bitfield(b"\x00")


def test_request_available_piece_sends_blocks_until_limit() -> None:
    piece_manager = FakePieceManager()
    peer = Peer(peer_id=b"peer", number_of_pieces=16, piece_manager=piece_manager)
    peer.peer_choking = False
    peer.bitfield = bytearray(b"\xff\xff")
    peer.tcp_protocol = SimpleNamespace(is_connected=True, send_message=AsyncMock())

    asyncio.run(peer._request_available_piece())

    assert piece_manager.acquire_piece.await_args.args[0] == bytes(peer.bitfield)
    assert peer.current_piece_index == 1
    assert len(peer.pending_requests) == 3
    assert peer.next_request_offset == BLOCK_SIZE * 2 + 16
    assert peer.tcp_protocol.send_message.await_count == 3
    first_message = peer.tcp_protocol.send_message.await_args_list[0].args[0]
    assert first_message.msg_type == MessageType.REQUEST
    assert first_message.payload[:4] == (1).to_bytes(4, "big")


def test_handle_message_state_transitions_and_cancel() -> None:
    piece_manager = FakePieceManager()
    peer = Peer(peer_id=b"peer", number_of_pieces=16, piece_manager=piece_manager)
    peer.current_piece_index = 3
    peer.current_piece_size = 8
    peer.pending_requests[(3, 0)] = 8
    peer.peer_choking = False
    peer._request_available_piece = AsyncMock()

    asyncio.run(peer.handle_message(Message(1, MessageType.CHOKE, b""), b""))
    assert peer.peer_choking is True
    assert peer.current_piece_index is None
    assert piece_manager.mark_piece_available.called

    asyncio.run(peer.handle_message(Message(1, MessageType.UNCHOKE, b""), b""))
    assert peer.peer_choking is False
    peer._request_available_piece.assert_awaited()

    asyncio.run(peer.handle_message(Message(1, MessageType.INTERESTED, b""), b""))
    assert peer.peer_interested is True
    asyncio.run(peer.handle_message(Message(1, MessageType.NOT_INTERESTED, b""), b""))
    assert peer.peer_interested is False

    asyncio.run(
        peer.handle_message(
            Message(5, MessageType.HAVE, (2).to_bytes(4, "big")), (2).to_bytes(4, "big")
        )
    )
    assert peer.bitfield[0] & 0b00100000

    before = bytes(peer.bitfield)
    asyncio.run(peer.handle_message(Message(5, MessageType.BITFIELD, b"\x00"), b"\x00"))
    assert bytes(peer.bitfield) == before

    asyncio.run(
        peer.handle_message(
            Message(5, MessageType.CANCEL, (7).to_bytes(4, "big")),
            (7).to_bytes(4, "big"),
        )
    )
    piece_manager.mark_piece_available.assert_called_with(7)


def test_handle_message_piece_completion_releases_current_piece() -> None:
    piece_manager = FakePieceManager()
    piece_manager.register_block = AsyncMock(return_value=True)
    peer = Peer(peer_id=b"peer", number_of_pieces=16, piece_manager=piece_manager)
    peer.peer_choking = False
    peer.current_piece_index = 4
    peer.current_piece_size = 8
    peer._request_available_piece = AsyncMock()

    payload = (4).to_bytes(4, "big") + (0).to_bytes(4, "big") + b"abcd"
    asyncio.run(
        peer.handle_message(
            Message(len(payload) + 1, MessageType.PIECE, payload), payload
        )
    )

    piece_manager.register_block.assert_awaited_once_with(4, b"abcd", offset=0)
    assert peer.current_piece_index is None
    peer._request_available_piece.assert_awaited()


def test_connect_async_success_and_failure(monkeypatch) -> None:
    class FakeProtocol:
        def __init__(self, peer: Peer, connected: bool = True) -> None:
            self.peer = peer
            self.is_connected = connected
            self.sent = []

        async def create_connection(
            self, ip: str, port: int, info_hash: bytes, peer_id: bytes
        ) -> None:
            self.is_connected = True

        async def send_message(self, message: Message) -> None:
            self.sent.append(message)

        async def receive_message(self):
            self.is_connected = False
            return None

        def close(self) -> None:
            self.is_connected = False

    fake_protocol = FakeProtocol(peer=None)  # type: ignore[arg-type]

    def protocol_factory(peer: Peer):
        fake_protocol.peer = peer
        return fake_protocol

    monkeypatch.setattr("src.peer.peer.TCPProtocol", protocol_factory)

    peer = Peer(peer_id=b"peer", ip="127.0.0.1", port=6881, number_of_pieces=8)
    connected = asyncio.run(peer.connect_async("127.0.0.1", 6881, b"hash"))
    assert connected is True
    assert peer.am_interested is True
    assert fake_protocol.sent[0].msg_type == MessageType.INTERESTED

    class ClosedProtocol(FakeProtocol):
        async def create_connection(
            self, ip: str, port: int, info_hash: bytes, peer_id: bytes
        ) -> None:
            self.is_connected = False

    closed_protocol = ClosedProtocol(peer=None)  # type: ignore[arg-type]

    def closed_factory(peer: Peer):
        closed_protocol.peer = peer
        return closed_protocol

    monkeypatch.setattr("src.peer.peer.TCPProtocol", closed_factory)

    peer = Peer(peer_id=b"peer", ip="127.0.0.1", port=6881, number_of_pieces=8)
    connected = asyncio.run(peer.connect_async("127.0.0.1", 6881, b"hash"))
    assert connected is False
    assert closed_protocol.sent == []
