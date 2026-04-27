from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

from src.peer.peer_manager import MAX_RETRY_BACKOFF_SECONDS, MIN_RETRY_BACKOFF_SECONDS, PeerManager


def test_add_remove_and_get_peers() -> None:
    manager = PeerManager()
    class FakePeer:
        def __init__(self, ip: str, port: int) -> None:
            self.ip = ip
            self.port = port

        def __hash__(self) -> int:
            return id(self)

    peer_a = FakePeer("127.0.0.1", 6881)
    peer_b = FakePeer("127.0.0.2", 6881)

    assert manager.add_peer(peer_a) is True
    assert manager.add_peer(peer_a) is False
    assert manager.add_peer(peer_b) is True
    assert manager.peer_count() == 2
    assert manager.get_peers() == {peer_a, peer_b}

    manager.remove_peer(peer_a)
    assert manager.peer_count() == 1
    assert manager.get_peers() == {peer_b}


def test_retry_backoff_caps_at_maximum() -> None:
    manager = PeerManager()

    assert manager._retry_backoff(0) == MIN_RETRY_BACKOFF_SECONDS
    assert manager._retry_backoff(20) == MAX_RETRY_BACKOFF_SECONDS


def test_connect_new_peers_starts_once_until_running_tasks_finish() -> None:
    manager = PeerManager()
    gate = asyncio.Event()

    class FakePeer:
        def __init__(self, ip: str, port: int) -> None:
            self.ip = ip
            self.port = port

        async def connect_async(self, ip: str, port: int, info_hash: bytes) -> bool:
            await gate.wait()
            return True

    peer_a = FakePeer("127.0.0.1", 6881)
    peer_b = FakePeer("127.0.0.2", 6881)
    manager.add_peer(peer_a)
    manager.add_peer(peer_b)

    async def run() -> None:
        started = await manager.connect_new_peers(b"hash")
        assert started == 2
        assert await manager.connect_new_peers(b"hash") == 0

        gate.set()
        await asyncio.sleep(0)
        await manager.shutdown()

    asyncio.run(run())


def test_connect_peer_task_records_failures_and_retry_delay(monkeypatch) -> None:
    manager = PeerManager()
    peer = SimpleNamespace(ip="127.0.0.1", port=6881)
    endpoint = (peer.ip, peer.port)
    manager.peers[endpoint] = peer
    manager._connect_peer = AsyncMock(return_value=False)
    monkeypatch.setattr("src.peer.peer_manager.time.monotonic", lambda: 100.0)

    asyncio.run(manager._connect_peer_task(endpoint, peer, b"hash"))

    assert manager._failure_count[endpoint] == 1
    assert manager._retry_after[endpoint] == 100.0 + MIN_RETRY_BACKOFF_SECONDS * 2
    assert endpoint not in manager._connect_tasks
