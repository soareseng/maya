from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import threading

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


# ---------------------------------------------------------------------------
# Tests for PR changes: asyncio.to_thread usage in register_block
# ---------------------------------------------------------------------------


def test_register_block_single_file_uses_asyncio_to_thread() -> None:
    """register_block must call save_piece via asyncio.to_thread, not directly."""
    manager = _make_manager()

    to_thread_calls = []

    async def fake_to_thread(func, **kwargs):
        to_thread_calls.append((func, kwargs))
        func(**kwargs)

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        asyncio.run(manager.register_block(index=0, data=b"abcd", offset=0))

    assert len(to_thread_calls) == 1
    called_func, called_kwargs = to_thread_calls[0]
    assert called_func == manager.file_manager.save_piece
    assert called_kwargs == {
        "piece_index": 0,
        "data": b"abcd",
        "file_path": "target.bin",
        "piece_length": 4,
        "offset": 0,
    }


def test_register_block_multi_file_uses_asyncio_to_thread() -> None:
    """register_block must call save_piece_to_files via asyncio.to_thread when file_layout is set."""
    file_layout = [
        {"path": "file-a.bin", "length": 2},
        {"path": "file-b.bin", "length": 2},
    ]
    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=FakeFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: set())),
        file_layout=file_layout,
    )

    to_thread_calls = []

    async def fake_to_thread(func, **kwargs):
        to_thread_calls.append((func, kwargs))
        func(**kwargs)

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        asyncio.run(manager.register_block(index=0, data=b"ab", offset=0))

    assert len(to_thread_calls) == 1
    called_func, called_kwargs = to_thread_calls[0]
    assert called_func == manager.file_manager.save_piece_to_files
    assert called_kwargs == {
        "piece_index": 0,
        "data": b"ab",
        "piece_length": 4,
        "files": file_layout,
        "offset": 0,
    }


def test_register_block_save_piece_runs_in_worker_thread() -> None:
    """save_piece must execute in a non-event-loop thread (asyncio.to_thread guarantee)."""
    event_loop_thread_id = None
    save_piece_thread_id = None

    class ThreadCapturingFileManager:
        def save_piece(self, **kwargs) -> None:
            nonlocal save_piece_thread_id
            save_piece_thread_id = threading.current_thread().ident

    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=ThreadCapturingFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: set())),
    )

    async def run():
        nonlocal event_loop_thread_id
        event_loop_thread_id = threading.current_thread().ident
        await manager.register_block(index=0, data=b"abcd", offset=0)

    asyncio.run(run())

    assert save_piece_thread_id is not None
    assert save_piece_thread_id != event_loop_thread_id


def test_register_block_save_piece_to_files_runs_in_worker_thread() -> None:
    """save_piece_to_files must execute in a non-event-loop thread."""
    event_loop_thread_id = None
    save_thread_id = None

    class ThreadCapturingFileManager:
        def save_piece_to_files(self, **kwargs) -> None:
            nonlocal save_thread_id
            save_thread_id = threading.current_thread().ident

    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=ThreadCapturingFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: set())),
        file_layout=[{"path": "file-a.bin", "length": 4}],
    )

    async def run():
        nonlocal event_loop_thread_id
        event_loop_thread_id = threading.current_thread().ident
        await manager.register_block(index=0, data=b"abcd", offset=0)

    asyncio.run(run())

    assert save_thread_id is not None
    assert save_thread_id != event_loop_thread_id


def test_register_block_awaits_to_thread_coroutine() -> None:
    """asyncio.to_thread must be awaited so the save completes before register_block returns."""
    save_called = []

    class SlowFileManager:
        def save_piece(self, **kwargs) -> None:
            save_called.append(True)

    manager = PieceManager(
        pieces=[b"hash0"],
        file_manager=SlowFileManager(),
        piece_length=4,
        total_length=4,
        target_file_path="target.bin",
        torrent=SimpleNamespace(peer_manager=SimpleNamespace(get_peers=lambda: set())),
    )

    asyncio.run(manager.register_block(index=0, data=b"abcd", offset=0))

    assert save_called, "save_piece must have been called before register_block returned"


# ---------------------------------------------------------------------------
# Tests for PR change: list() copy in acquire_piece prevents RuntimeError
# ---------------------------------------------------------------------------


def test_acquire_piece_no_runtime_error_when_removing_from_available() -> None:
    """acquire_piece must not raise RuntimeError when it removes an idx from self.available
    while iterating — the list() copy introduced in the PR makes this safe."""
    # All pieces available; peer has piece 0 (bitfield 0b10000000 = 0x80)
    manager = _make_manager(total_length=4, piece_length=2, pieces_count=2)
    assert 0 in manager.available and 1 in manager.available

    # Should not raise RuntimeError: Set changed size during iteration
    result = asyncio.run(manager.acquire_piece(b"\x80"))

    assert result == 0
    assert 0 not in manager.available
    assert 0 in manager.in_progress


def test_acquire_piece_list_copy_allows_full_iteration_after_first_match() -> None:
    """The list() copy must allow iteration to complete without error even if
    self.available is modified for the first matching piece."""
    # Four pieces: peer has pieces 1, 2, 3 but NOT piece 0
    # Bitfield: 0b01110000 = 0x70
    manager = _make_manager(total_length=8, piece_length=2, pieces_count=4)

    # Force the set to be ordered so piece 0 is encountered before piece 1.
    # Sets in CPython are unordered, so we clear and rebuild deterministically
    # via multiple single-element calls to acquire_piece — or we test that
    # regardless of which piece is returned, no exception is raised.
    result = asyncio.run(manager.acquire_piece(b"\x70"))

    # Peer has pieces 1, 2, 3; one of them must be acquired
    assert result in {1, 2, 3}
    assert result not in manager.available
    assert result in manager.in_progress


def test_acquire_piece_modifies_available_set_safely_with_many_pieces() -> None:
    """Regression: iterating over a set while removing an element used to raise
    RuntimeError. With list() copy the loop completes correctly for large sets."""
    num_pieces = 20
    # peer has all pieces: build bitfield of ceil(20/8)=3 bytes all set to 0xFF
    bitfield = bytes([0xFF, 0xFF, 0xFF])
    manager = _make_manager(
        total_length=num_pieces * 4,
        piece_length=4,
        pieces_count=num_pieces,
    )
    assert len(manager.available) == num_pieces

    result = asyncio.run(manager.acquire_piece(bitfield))

    assert result is not None
    assert result not in manager.available
    assert result in manager.in_progress
    # Exactly one piece removed from available
    assert len(manager.available) == num_pieces - 1


def test_acquire_piece_returns_none_when_no_pieces_available() -> None:
    """acquire_piece must return None when available set is empty (regression/boundary)."""
    manager = _make_manager()
    manager.available.clear()

    result = asyncio.run(manager.acquire_piece(b"\xFF"))

    assert result is None


def test_acquire_piece_skips_already_downloaded_pieces() -> None:
    """acquire_piece must skip pieces that are already in downloaded even if in available."""
    manager = _make_manager(total_length=8, piece_length=4, pieces_count=2)
    manager.downloaded.add(0)

    # Peer has both pieces (0xFF)
    result = asyncio.run(manager.acquire_piece(b"\xFF"))

    # Piece 0 is downloaded, so only piece 1 may be returned
    assert result == 1
