import asyncio
import socket
from typing import Any

from src.utils.logger import logger
from .message import Message, MessageType

CONNECT_TIMEOUT_SECONDS = 120
READ_TIMEOUT_SECONDS = 120
MAX_BLOCK_SIZE = 16 * 1024
MAX_MESSAGE_SIZE = 10 * 1024 * 1024
HANDSHAKE_LENGTH = 68
PSTRLEN = 19


class TCPProtocol:
    def __init__(self, peer: Any):
        self.peer = peer
        self.socket = self._create_socket()
        self.is_connected = False

    def _create_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        return sock

    async def _recv_exact(self, size: int) -> bytes:
        chunks = []
        bytes_recd = 0
        loop = asyncio.get_running_loop()

        while bytes_recd < size:
            try:
                chunk = await asyncio.wait_for(
                    loop.sock_recv(self.socket, size - bytes_recd),
                    timeout=READ_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("Timeout while receiving data")
                self.is_connected = False
                return b""

            if not chunk:
                return b""

            chunks.append(chunk)
            bytes_recd += len(chunk)

        return b"".join(chunks)

    async def _send_handshake(self, info_hash: bytes, peer_id: bytes) -> None:
        handshake_message = self.peer.handshake(info_hash=info_hash, peer_id=peer_id)
        await asyncio.get_running_loop().sock_sendall(self.socket, handshake_message)

        logger.info(
            f"Handshake sent to {self.peer.ip}:{self.peer.port} - Peer ID: {peer_id.hex()}"
        )

    async def _wait_for_handshake(self, expected_info_hash: bytes) -> bool:
        response = await self._recv_exact(HANDSHAKE_LENGTH)

        if len(response) < HANDSHAKE_LENGTH:
            logger.error("Handshake too short")
            return False

        if response[0] != PSTRLEN:
            logger.error("Invalid pstrlen")
            return False

        if response[1:20] != b"BitTorrent protocol":
            logger.error("Invalid protocol")
            return False

        info_hash = response[28:48]
        peer_id = response[48:68]

        if info_hash != expected_info_hash:
            logger.error("Invalid info_hash")
            return False

        logger.info(
            f"Handshake received from {self.peer.ip}:{self.peer.port} - Peer ID: {peer_id.hex()}"
        )

        return True

    async def create_connection(
        self, ip: str, port: int, info_hash: bytes, peer_id: bytes
    ) -> None:
        loop = asyncio.get_running_loop()

        try:
            await asyncio.wait_for(
                loop.sock_connect(self.socket, (ip, port)),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )

            self.is_connected = True
            logger.info(f"Connected to {ip}:{port}")

            await self._send_handshake(info_hash, peer_id)

            ok = await self._wait_for_handshake(info_hash)
            if not ok:
                self.close()
                logger.error(f"Handshake failed with {ip}:{port}")
                return

        except (OSError, asyncio.TimeoutError) as exc:
            logger.error(f"Connection failed {ip}:{port}: {exc}")
            self.close()

    async def send_message(self, message: Message) -> None:
        if not self.is_connected:
            logger.warning("Send attempted without connection")
            return

        try:
            await asyncio.get_running_loop().sock_sendall(
                self.socket, message.to_bytes()
            )
        except Exception as e:
            logger.error(f"Send error: {e}")
            self.close()

    async def receive_message(self) -> Message | None:
        try:
            header = await self._recv_exact(4)

            if not header:
                logger.info("Peer closed connection")
                self.close()
                return None

            length = int.from_bytes(header, "big")

            if length == 0:
                return None

            if length > MAX_MESSAGE_SIZE:
                logger.error(f"Message too large: {length}")
                self.close()
                return None

            message_id = await self._recv_exact(1)
            if not message_id:
                self.close()
                return None

            payload = await self._recv_exact(length - 1) if length > 1 else b""

            try:
                return Message(length, MessageType(message_id[0]), payload)
            except ValueError:
                logger.warning(f"Unknown message: {message_id[0]}")
                return None

        except Exception as e:
            logger.error(f"Receive error: {e}")
            self.close()
            return None

    def close(self) -> None:
        if self.is_connected:
            logger.info(f"Closing connection {self.peer.ip}:{self.peer.port}")

        self.is_connected = False

        try:
            self.socket.close()
        except OSError:
            pass
