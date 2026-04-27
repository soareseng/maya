from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio

from src.piece.piece_manager import PieceManager
from src.peer.message import MessageType


class FakeFileManager:
    def __init__(self) -> None:
        self.saved_piece_calls = []
        self.saved_piece_to_files_calls = []

    def save_piece(self, **kwargs) -> None:
        self.saved_piece_calls.append(kwargs)

    def save_piece_to_files(self, **kwargs) -> None:
        self.saved_piece_to_files_calls.append(kwargs)


def _make_manager(
    total_length: int = 8,
    piece_length: int = 4,
    torrent=None,
    pieces_count: int = 2,
) -> PieceManager:
    torrent = torrent or SimpleNamespace(
        peer_manager=SimpleNamespace(get_peers=lambda: set())
    )
    return PieceManager(
        pieces=[b"hash"] * pieces_count,
        file_manager=FakeFileManager(),
        piece_length=piece_length,
        total_length=total_length,
        target_file_path="target.bin",
        torrent=torrent,
    )


def test_piece_size_and_progress_helpers() -> None:
    manager = _make_manager(total_length=10, piece_length=4, pieces_count=3)

    assert manager.get_piece_size(0) == 4
    assert manager.get_piece_size(1) == 4
    assert manager.get_piece_size(2) == 2
    assert manager.get_progress_percent() == 0.0
    assert not manager.is_complete()


def test_acquire_piece_respects_bitfield_and_updates_state() -> None:
    manager = _make_manager()

    piece_index = asyncio.run(manager.acquire_piece(b"\x80"))

    assert piece_index == 0
    assert manager.available == {1}
    assert manager.in_progress == {0}


def test_register_block_completes_piece_and_sends_have() -> None:
    class FakePeer:
        def __init__(self) -> None:
            self.peer_id = b"peer-1"
            self.tcp_protocol = SimpleNamespace(
                is_connected=True, send_message=AsyncMock()
            )

        def __hash__(self) -> int:
            return id(self)

    peer_protocol = SimpleNamespace(is_connected=True, send_message=AsyncMock())
    peer = FakePeer()
    peer.tcp_protocol = peer_protocol
    torrent = SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: {peer}))
    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=FakeFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=torrent,
    )

    complete = asyncio.run(manager.register_block(index=0, data=b"abcd", offset=0))

    assert complete is True
    assert manager.downloaded == {0}
    assert manager.get_downloaded_bytes() == 4
    assert manager.is_complete()
    assert manager.file_manager.saved_piece_calls == [
        {
            "piece_index": 0,
            "data": b"abcd",
            "file_path": "target.bin",
            "piece_length": 4,
            "offset": 0,
        }
    ]
    peer_protocol.send_message.assert_awaited_once()
    sent_message = peer_protocol.send_message.await_args.args[0]
    assert sent_message.msg_type == MessageType.HAVE
    assert sent_message.payload == (0).to_bytes(4, "big")


def test_register_block_uses_multi_file_layout_when_present() -> None:
    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=FakeFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: set())),
        file_layout=[
            {"path": "file-a.bin", "length": 2},
            {"path": "file-b.bin", "length": 2},
        ],
    )

    complete = asyncio.run(manager.register_block(index=0, data=b"ab", offset=0))

    assert complete is False
    assert manager.file_manager.saved_piece_to_files_calls == [
        {
            "piece_index": 0,
            "data": b"ab",
            "piece_length": 4,
            "files": [
                {"path": "file-a.bin", "length": 2},
                {"path": "file-b.bin", "length": 2},
            ],
            "offset": 0,
        }
    ]


def test_mark_piece_available_and_unavailable() -> None:
    manager = _make_manager()
    manager.in_progress.add(0)

    manager.mark_piece_available(0)
    assert 0 in manager.available
    assert 0 not in manager.in_progress

    manager.mark_piece_unavailable(0)
    assert 0 not in manager.available
