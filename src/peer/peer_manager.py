import asyncio
import time

from src.utils.logger import logger

MIN_RETRY_BACKOFF_SECONDS = 5
MAX_RETRY_BACKOFF_SECONDS = 90


class PeerManager:
    def __init__(self):
        self.peers: dict[tuple[str, int], object] = {}
        self._connect_tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._retry_after: dict[tuple[str, int], float] = {}
        self._failure_count: dict[tuple[str, int], int] = {}

    def add_peer(self, peer) -> bool:
        endpoint = (peer.ip, peer.port)
        if endpoint in self.peers:
            return False
        self.peers[endpoint] = peer
        return True

    def remove_peer(self, peer):
        endpoint = (peer.ip, peer.port)
        self.peers.pop(endpoint, None)
        self._retry_after.pop(endpoint, None)
        self._failure_count.pop(endpoint, None)

        task = self._connect_tasks.pop(endpoint, None)
        if task and not task.done():
            task.cancel()

    def peer_count(self) -> int:
        return len(self.peers)

    def get_peers(self):
        return set(self.peers.values())

    def _retry_backoff(self, failures: int) -> int:
        return min(MAX_RETRY_BACKOFF_SECONDS, MIN_RETRY_BACKOFF_SECONDS * (2**failures))

    async def _connect_peer(self, peer, info_hash: bytes) -> bool:
        try:
            return await peer.connect_async(peer.ip, peer.port, info_hash)
        except Exception as exc:
            logger.error(f"Unexpected peer task failure {peer.ip}:{peer.port}: {exc}")
            return False

    async def _connect_peer_task(
        self, endpoint: tuple[str, int], peer, info_hash: bytes
    ) -> None:
        try:
            connected = await self._connect_peer(peer, info_hash)
            now = time.monotonic()

            if connected:
                self._failure_count[endpoint] = 0
                self._retry_after[endpoint] = now + MIN_RETRY_BACKOFF_SECONDS
                return

            failures = self._failure_count.get(endpoint, 0) + 1
            self._failure_count[endpoint] = failures
            self._retry_after[endpoint] = now + self._retry_backoff(failures)
        finally:
            self._connect_tasks.pop(endpoint, None)

    async def connect_new_peers(self, info_hash: bytes) -> int:
        started = 0
        now = time.monotonic()

        for endpoint, peer in self.peers.items():
            task = self._connect_tasks.get(endpoint)
            if task and not task.done():
                continue

            retry_at = self._retry_after.get(endpoint, 0.0)
            if now < retry_at:
                continue

            self._connect_tasks[endpoint] = asyncio.create_task(
                self._connect_peer_task(endpoint, peer, info_hash)
            )
            started += 1

        return started

    async def shutdown(self) -> None:
        running = [task for task in self._connect_tasks.values() if not task.done()]
        for task in running:
            task.cancel()

        if running:
            await asyncio.gather(*running, return_exceptions=True)

        self._connect_tasks.clear()
