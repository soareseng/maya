import asyncio
from typing import Dict
from src.peer.message import MessageType, Message

from src.utils.logger import logger


class PieceManager:
    def __init__(
        self,
        pieces: list[bytes],
        file_manager,
        piece_length: int,
        total_length: int,
        target_file_path: str,
        torrent,
        file_layout: list[dict] | None = None,
    ):
        self.pieces = pieces
        self.downloaded: set[int] = set()
        self.available: set[int] = set(range(len(pieces)))
        self.in_progress: set[int] = set()
        self.total_pieces = len(pieces)
        self.file_manager = file_manager
        self.piece_length = piece_length
        self.total_length = total_length
        self.target_file_path = target_file_path
        self.file_layout = file_layout or []
        self._downloaded_bytes = 0
        self._last_reported_percent = -1
        self._lock = asyncio.Lock()
        self.blocks: Dict[int, Dict[int, int]] = {}
        self._received_bytes_per_piece: dict[int, int] = {}
        self.torrent = torrent

    def register_piece_hash(self, index: int, piece_hash: bytes) -> None:
        self.pieces[index] = piece_hash

    def _piece_size(self, index: int) -> int:
        if index < self.total_pieces - 1:
            return self.piece_length
        consumed = self.piece_length * (self.total_pieces - 1)
        return self.total_length - consumed

    def get_piece_size(self, index: int) -> int:
        return self._piece_size(index)

    async def register_block(
        self,
        index: int,
        data: bytes,
        offset: int,
    ) -> bool:
        async with self._lock:
            if index in self.downloaded:
                return False

            if index not in self.blocks:
                self.blocks[index] = {}

            if offset in self.blocks[index]:
                return False

            self.blocks[index][offset] = len(data)
            self._downloaded_bytes += len(data)
            self._received_bytes_per_piece[index] = (
                self._received_bytes_per_piece.get(index, 0) + len(data)
            )

            piece_size = self._piece_size(index)
            received = self._received_bytes_per_piece[index]

        if self.file_layout:
            self.file_manager.save_piece_to_files(
                piece_index=index,
                data=data,
                piece_length=self.piece_length,
                files=self.file_layout,
                offset=offset,
            )
        else:
            self.file_manager.save_piece(
                piece_index=index,
                data=data,
                file_path=self.target_file_path,
                piece_length=self.piece_length,
                offset=offset,
            )

        if received >= piece_size:
            await self.mark_piece_downloaded(index)
            self.blocks.pop(index, None)
            self._received_bytes_per_piece.pop(index, None)
            return True

        return False

    def get_piece(self, index: int) -> bytes:
        return self.pieces[index]

    async def acquire_piece(self, peer_bitfield: bytes) -> int | None:
        async with self._lock:
            for idx in self.available:
                if idx in self.downloaded:
                    continue

                byte_index = idx // 8
                bit_index = idx % 8

                if byte_index >= len(peer_bitfield):
                    continue

                has_piece = (peer_bitfield[byte_index] >> (7 - bit_index)) & 1

                if has_piece:
                    self.available.remove(idx)
                    self.in_progress.add(idx)
                    self.blocks[idx] = {}
                    return idx

        return None

    async def mark_piece_downloaded(self, index: int) -> None:
        if index in self.downloaded:
            return

        self.in_progress.discard(index)
        self.downloaded.add(index)

        if self.total_pieces == 0:
            return

        progress = (len(self.downloaded) / self.total_pieces) * 100
        progress_int = int(progress)
        if progress_int > self._last_reported_percent:
            self._last_reported_percent = progress_int
            logger.info(
                f"[PROGRESS] {progress:.2f}% ({len(self.downloaded)}/{self.total_pieces} pieces)"
            )
        connected_peers = self.torrent.peer_manager.get_peers()
        tasks = []
        for peer in connected_peers:
            if peer.tcp_protocol and peer.tcp_protocol.is_connected:
                message = Message(
                    msg_length=5,
                    msg_type=MessageType.HAVE,
                    payload=index.to_bytes(4, "big"),
                )
                logger.debug(f"Sending HAVE for piece {index} to {peer.peer_id.hex()}")
                tasks.append(peer.tcp_protocol.send_message(message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def mark_piece_available(self, index: int) -> None:
        self.in_progress.discard(index)
        self.available.add(index)

    def mark_piece_unavailable(self, index: int) -> None:
        self.available.discard(index)

    def is_complete(self) -> bool:
        return len(self.downloaded) == self.total_pieces

    def get_downloaded_bytes(self) -> int:
        return self._downloaded_bytes

    def get_progress_percent(self) -> float:
        if self.total_length <= 0:
            return 0.0
        return min(100.0, (self.get_downloaded_bytes() / self.total_length) * 100)
