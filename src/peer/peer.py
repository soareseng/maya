import asyncio
from typing import Any

from src.peer.message import Message, MessageType
from src.peer.tcp_protocol import TCPProtocol
from src.utils.logger import logger

BLOCK_SIZE = 16384
MAX_INFLIGHT_REQUESTS = 10
PEER_MESSAGE_IDLE_TIMEOUT_SECONDS = 30
MAX_PENDING_STALL_TIMEOUTS = 2

MESSAGE_TO_FUNC_MAPPER = {
    MessageType.CHOKE: {
        "func": "process_choke",
        "is_async": False,
        "expects_payload": False,
    },
    MessageType.UNCHOKE: {
        "func": "process_unchoke",
        "is_async": True,
        "expects_payload": False,
    },
    MessageType.INTERESTED: {
        "func": "process_interested",
        "is_async": False,
        "expects_payload": False,
    },
    MessageType.NOT_INTERESTED: {
        "func": "process_not_interested",
        "is_async": False,
        "expects_payload": False,
    },
    MessageType.HAVE: {
        "func": "process_have",
        "is_async": True,
        "expects_payload": True,
    },
    MessageType.BITFIELD: {
        "func": "process_bitfield",
        "is_async": True,
        "expects_payload": True,
    },
    MessageType.PIECE: {
        "func": "process_piece",
        "is_async": True,
        "expects_payload": True,
    },
    MessageType.CANCEL: {
        "func": "process_cancel",
        "is_async": False,
        "expects_payload": True,
    },
}


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

        self.pending_requests: dict[tuple[int, int], int] = {}
        self.pending_stall_count = 0

    def update_bitfield(self, payload: bytes) -> bytes:
        expected_size = (self.number_of_pieces + 7) // 8
        if expected_size and len(payload) != expected_size:
            raise ValueError(
                f"Invalid bitfield size. Expected {expected_size}, got {len(payload)}"
            )
        self.bitfield = bytearray(payload)
        return payload

    def _set_piece_in_bitfield(self, piece_index: int) -> None:
        byte_index = piece_index // 8
        bit_index = piece_index % 8
        if byte_index < len(self.bitfield):
            self.bitfield[byte_index] |= 1 << (7 - bit_index)

    def handshake(self, info_hash: bytes, peer_id: bytes) -> bytes:
        return b"\x13BitTorrent protocol" + b"\x00" * 8 + info_hash + peer_id

    def _release_current_piece(self) -> None:
        if self.current_piece_index is not None and self.piece_manager:
            if self.current_piece_index not in self.piece_manager.downloaded:
                self.piece_manager.mark_piece_available(self.current_piece_index)

        self.current_piece_index = None
        self.current_piece_size = 0
        self.next_request_offset = 0
        self.pending_requests.clear()

    async def _request_available_piece(self) -> None:
        if not self.piece_manager or not self.tcp_protocol or self.peer_choking:
            return

        if self.current_piece_index is None:
            piece = await self.piece_manager.acquire_piece(bytes(self.bitfield))
            if piece is None:
                return

            self.current_piece_index = piece
            self.current_piece_size = self.piece_manager.get_piece_size(piece)
            self.next_request_offset = 0
            self.pending_requests.clear()
            logger.debug(f"Requesting piece {piece} from {self.peer_id.hex()}")

        while (
            len(self.pending_requests) < MAX_INFLIGHT_REQUESTS
            and self.next_request_offset < self.current_piece_size
        ):
            logger.debug(
                f"Requesting block at offset {self.next_request_offset} of piece {self.current_piece_index} from {self.peer_id.hex()}"
            )
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
            self.pending_requests[(self.current_piece_index, offset)] = size
            self.next_request_offset += size

    def process_choke(self) -> None:
        self.peer_choking = True
        self._release_current_piece()

    async def process_unchoke(self) -> None:
        self.peer_choking = False
        await self._request_available_piece()

    def process_interested(self) -> None:
        self.peer_interested = True

    def process_not_interested(self) -> None:
        self.peer_interested = False

    async def process_have(self, payload: bytes) -> None:
        if len(payload) != 4:
            return

        piece_index = int.from_bytes(payload, "big")

        if piece_index >= self.number_of_pieces:
            return

        self._set_piece_in_bitfield(piece_index)
        await self._request_available_piece()

    async def process_bitfield(self, payload: bytes) -> None:
        try:
            logger.debug(f"Received bitfield from {self.peer_id.hex()}")
            self.update_bitfield(payload)
            await self._request_available_piece()
        except ValueError as e:
            logger.error(e)

    async def process_piece(self, payload: bytes) -> None:
        if len(payload) < 8:
            return

        piece_index = int.from_bytes(payload[:4], "big")
        offset = int.from_bytes(payload[4:8], "big")
        block = payload[8:]

        if self.piece_manager:
            complete = await self.piece_manager.register_block(
                piece_index, block, offset=offset
            )

            self.pending_requests.pop((piece_index, offset), None)

            if complete:
                self._release_current_piece()
                if not self.peer_choking:
                    await self._request_available_piece()
            else:
                await self._request_available_piece()

    def process_cancel(self, payload: bytes) -> None:
        if len(payload) < 8:
            return

        piece_index = int.from_bytes(payload[:4], "big")
        offset = int.from_bytes(payload[4:8], "big")

        self.pending_requests.pop((piece_index, offset), None)

    async def handle_message(self, message: Message, payload: bytes) -> None:
        msg_type = message.msg_type
        message_handler = MESSAGE_TO_FUNC_MAPPER.get(msg_type)
        if message_handler is None:
            logger.warning(f"Unknown message type {msg_type} from {self.peer_id.hex()}")
            return
        func_name = message_handler["func"]
        is_async = message_handler["is_async"]
        expects_payload = message_handler["expects_payload"]
        func = getattr(self, func_name, None)
        if func is None:
            logger.warning(
                f"No handler function {func_name} for message type {msg_type}"
            )
            return
        if expects_payload and not payload:
            logger.warning(
                f"Message type {msg_type} from {self.peer_id.hex()} expected payload but got none"
            )
            return
        if is_async:
            await func(payload) if expects_payload else await func()
        else:
            func(payload) if expects_payload else func()

    async def connect_async(self, ip: str, port: int, info_hash: bytes) -> bool:
        self.tcp_protocol = TCPProtocol(self)
        connected_once = False

        await self.tcp_protocol.create_connection(ip, port, info_hash, self.peer_id)

        if not self.tcp_protocol.is_connected:
            return False

        connected_once = True
        message = Message(msg_length=1, msg_type=MessageType.INTERESTED, payload=b"")
        await self.tcp_protocol.send_message(message)
        self.am_interested = True

        try:
            while self.tcp_protocol.is_connected:
                try:
                    message = await asyncio.wait_for(
                        self.tcp_protocol.receive_message(),
                        timeout=PEER_MESSAGE_IDLE_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await self.tcp_protocol.send_keepalive()

                    if self.pending_requests:
                        self.pending_stall_count += 1
                    else:
                        self.pending_stall_count = 0

                    if self.pending_stall_count >= MAX_PENDING_STALL_TIMEOUTS:
                        logger.warning(
                            f"Peer stalled on piece {self.current_piece_index}; reassigning pending blocks from {self.peer_id.hex()}"
                        )
                        self._release_current_piece()
                        self.tcp_protocol.close()
                        break

                    continue

                self.pending_stall_count = 0

                if message is not None:
                    await self.handle_message(message, message.payload)
                else:
                    await asyncio.sleep(0)

        finally:
            self._release_current_piece()
            if self.tcp_protocol:
                self.tcp_protocol.close()

        return connected_once
