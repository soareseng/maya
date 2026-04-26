import asyncio
import time
from typing import Any

from src.peer.message import Message, MessageType
from src.peer.tcp_protocol import TCPProtocol
from src.utils.logger import logger

BLOCK_SIZE = 16384
MAX_INFLIGHT_REQUESTS = 8


class Peer:
    def __init__(
        self,
        peer_id: bytes,
        ip: str = "",
        port: int = 0,
        number_of_pieces: int = 0,
        piece_manager: Any = None,
    ):
        self.peer_id = peer_id
        self.ip = ip
        self.port = port
        self.number_of_pieces = max(0, number_of_pieces)
        self.piece_manager = piece_manager

        self.am_choking = True
        self.am_interested = False
        self.peer_choking = True
        self.peer_interested = False

        bitfield_size = (self.number_of_pieces + 7) // 8
        self.bitfield = bytearray(bitfield_size)

        self.tcp_protocol: TCPProtocol | None = None

        self.current_piece_index: int | None = None
        self.current_piece_size = 0
        self.next_request_offset = 0

        self.pending_requests: dict[int, int] = {}
        self.last_request_time = 0

    def update_bitfield(self, payload: bytes) -> None:
        expected_size = (self.number_of_pieces + 7) // 8
        if expected_size and len(payload) != expected_size:
            raise ValueError(
                f"Invalid bitfield size. Expected {expected_size}, got {len(payload)}"
            )
        self.bitfield = bytearray(payload)

    def _set_piece_in_bitfield(self, piece_index: int) -> None:
        byte_index = piece_index // 8
        bit_index = piece_index % 8
        if byte_index < len(self.bitfield):
            self.bitfield[byte_index] |= 1 << (7 - bit_index)

    def handshake(self, info_hash: bytes, peer_id: bytes) -> bytes:
        return b"\x13BitTorrent protocol" + b"\x00" * 8 + info_hash + peer_id

    def _release_current_piece(self) -> None:
        if self.current_piece_index is not None and self.piece_manager:
            if self.current_piece_index not in self.piece_manager.downloaded_pieces:
                self.piece_manager.mark_piece_available(self.current_piece_index)

        self.current_piece_index = None
        self.current_piece_size = 0
        self.next_request_offset = 0
        self.pending_requests.clear()

    async def _request_available_piece(self) -> None:
        if not self.piece_manager or not self.tcp_protocol or self.peer_choking:
            return

        now = asyncio.get_event_loop().time()
        if now - self.last_request_time < 0.01:
            return
        self.last_request_time = now

        if self.current_piece_index is None:
            piece = await self.piece_manager.find_next_piece(bytes(self.bitfield))
            if piece is None:
                return

            self.current_piece_index = piece
            self.current_piece_size = self.piece_manager.get_piece_size(piece)
            self.next_request_offset = 0
            self.pending_requests.clear()
            logger.info(f"Requesting piece {piece} from {self.peer_id.hex()}")

        while (
            len(self.pending_requests) < MAX_INFLIGHT_REQUESTS
            and self.next_request_offset < self.current_piece_size
        ):
            offset = self.next_request_offset
            size = min(BLOCK_SIZE, self.current_piece_size - offset)

            payload = (
                self.current_piece_index.to_bytes(4, "big")
                + offset.to_bytes(4, "big")
                + size.to_bytes(4, "big")
            )

            await self.tcp_protocol.send_message(
                Message(
                    msg_length=len(payload) + 1,
                    msg_type=MessageType.REQUEST,
                    payload=payload,
                )
            )
            self.pending_requests[offset] = size
            self.next_request_offset += size

    async def handle_message(self, message: Message, payload: bytes) -> None:
        msg_type = message.msg_type
        if msg_type == MessageType.CHOKE:
            self.peer_choking = True
            self._release_current_piece()

        elif msg_type == MessageType.UNCHOKE:
            self.peer_choking = False
            await self._request_available_piece()

        elif msg_type == MessageType.INTERESTED:
            self.peer_interested = True

        elif msg_type == MessageType.NOT_INTERESTED:
            self.peer_interested = False

        elif msg_type == MessageType.HAVE:
            if len(payload) != 4:
                return

            piece_index = int.from_bytes(payload, "big")

            if piece_index >= self.number_of_pieces:
                return

            self._set_piece_in_bitfield(piece_index)
            await self._request_available_piece()

        elif msg_type == MessageType.BITFIELD:
            try:
                self.update_bitfield(payload)
                await self._request_available_piece()
            except ValueError as e:
                logger.error(e)

        elif msg_type == MessageType.PIECE:
            if len(payload) < 8:
                return

            piece_index = int.from_bytes(payload[:4], "big")
            offset = int.from_bytes(payload[4:8], "big")
            block = payload[8:]

            if self.piece_manager:
                complete = self.piece_manager.register_piece(
                    piece_index, block, offset=offset
                )

                self.pending_requests.pop(offset, None)

                if complete:
                    logger.info(
                        f"Completed piece {piece_index} from {self.peer_id.hex()}"
                    )
                    self._release_current_piece()
                    message = Message(
                        msg_length=5,
                        msg_type=MessageType.HAVE,
                        payload=piece_index.to_bytes(4, "big"),
                    )
                    logger.info(
                        f"Sending HAVE for piece {piece_index} to {self.peer_id.hex()}"
                    )
                    await self.tcp_protocol.send_message(message)
                    if not self.peer_choking:
                        await self._request_available_piece()
                else:
                    await self._request_available_piece()

        elif msg_type == MessageType.CANCEL:
            if len(payload) >= 4 and self.piece_manager:
                piece = int.from_bytes(payload[:4], "big")
                self.piece_manager.mark_piece_available(piece)

    async def connect_async(self, ip: str, port: int, info_hash: bytes) -> None:
        self.tcp_protocol = TCPProtocol(self)

        await self.tcp_protocol.create_connection(ip, port, info_hash, self.peer_id)

        if not self.tcp_protocol.is_connected:
            return
        message = Message(msg_length=1, msg_type=MessageType.INTERESTED, payload=b"")
        await self.tcp_protocol.send_message(message)
        self.am_interested = True

        try:
            while self.tcp_protocol.is_connected:
                message = await self.tcp_protocol.receive_message()

                if message is not None:
                    await self.handle_message(message, message.payload)
                else:
                    await asyncio.sleep(0)

        finally:
            self._release_current_piece()
            if self.tcp_protocol:
                self.tcp_protocol.close()
