import os
import random
import socket
import struct
from urllib.parse import urlparse

from src.tracker.tracker import Tracker
from src.utils.logger import logger


class UDPTracker(Tracker):
    DEFAULT_TIMEOUT_SECONDS = 8
    DEFAULT_RETRIES = 2
    CONNECT_ACTION = 0
    ANNOUNCE_ACTION = 1
    PROTOCOL_ID = 0x41727101980

    def __init__(self):
        self.timeout_seconds = self.DEFAULT_TIMEOUT_SECONDS
        self.retries = self.DEFAULT_RETRIES

    def _transaction_id(self) -> int:
        return random.randint(0, 0xFFFFFFFF)

    def _parse_endpoint(self, url: str) -> tuple[str, int]:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "udp":
            raise ValueError(f"Unsupported UDP tracker scheme: {parsed.scheme}")
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"Invalid UDP tracker URL: {url}")
        return parsed.hostname, parsed.port

    def _connect(self, sock: socket.socket, endpoint: tuple[str, int]) -> int:
        transaction_id = self._transaction_id()
        request = struct.pack("!QII", self.PROTOCOL_ID, self.CONNECT_ACTION, transaction_id)
        sock.sendto(request, endpoint)

        response, _ = sock.recvfrom(2048)
        if len(response) < 16:
            raise ConnectionError("UDP tracker connect response too short")

        action, resp_txn_id, connection_id = struct.unpack("!IIQ", response[:16])
        if action != self.CONNECT_ACTION:
            raise ConnectionError(f"Unexpected UDP tracker connect action: {action}")
        if resp_txn_id != transaction_id:
            raise ConnectionError("UDP tracker transaction ID mismatch on connect")

        return connection_id

    def _build_announce_request(
        self,
        connection_id: int,
        info_hash: bytes,
        peer_id: bytes,
        port: int,
        uploaded: int,
        downloaded: int,
        left: int,
        numwant: int,
    ) -> tuple[bytes, int]:
        transaction_id = self._transaction_id()
        event = 2  # started
        ip_address = 0
        key = int.from_bytes(os.urandom(4), "big")
        numwant_value = -1 if numwant <= 0 else numwant

        request = struct.pack(
            "!QII20s20sQQQIIIIH",
            connection_id,
            self.ANNOUNCE_ACTION,
            transaction_id,
            info_hash,
            peer_id,
            downloaded,
            left,
            uploaded,
            event,
            ip_address,
            key,
            numwant_value & 0xFFFFFFFF,
            port,
        )
        return request, transaction_id

    def _parse_announce_response(
        self,
        response: bytes,
        expected_transaction_id: int,
    ) -> dict[str, int | bytes]:
        if len(response) < 20:
            raise ConnectionError("UDP tracker announce response too short")

        action, transaction_id, interval, leechers, seeders = struct.unpack(
            "!IIIII", response[:20]
        )
        if action != self.ANNOUNCE_ACTION:
            raise ConnectionError(f"Unexpected UDP tracker announce action: {action}")
        if transaction_id != expected_transaction_id:
            raise ConnectionError("UDP tracker transaction ID mismatch on announce")

        peers = response[20:]
        return {
            "peers": peers,
            "interval": interval,
            "complete": seeders,
            "incomplete": leechers,
        }

    def announce(
        self,
        url: str,
        info_hash: bytes,
        peer_id: bytes,
        port: int,
        uploaded: int = 0,
        downloaded: int = 0,
        left: int = 0,
        numwant: int = 50,
    ) -> dict[str, int | bytes]:
        endpoint = self._parse_endpoint(url)
        errors: list[Exception] = []

        for attempt in range(1, self.retries + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout_seconds)
            try:
                connection_id = self._connect(sock, endpoint)
                request, transaction_id = self._build_announce_request(
                    connection_id=connection_id,
                    info_hash=info_hash,
                    peer_id=peer_id,
                    port=port,
                    uploaded=uploaded,
                    downloaded=downloaded,
                    left=left,
                    numwant=numwant,
                )
                sock.sendto(request, endpoint)
                response, _ = sock.recvfrom(65536)
                return self._parse_announce_response(response, transaction_id)
            except (OSError, ConnectionError, ValueError) as exc:
                errors.append(exc)
                logger.warning(
                    f"UDP tracker attempt {attempt}/{self.retries} failed for {url}: {exc}"
                )
            finally:
                sock.close()

        last_error = errors[-1] if errors else RuntimeError("Unknown UDP tracker error")
        raise ConnectionError(f"Error connecting to UDP tracker {url}: {last_error}")
