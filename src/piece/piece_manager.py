import asyncio

from src.utils.logger import logger


class PieceManager:
    def __init__(
        self,
        pieces: list[bytes],
        file_manager,
        piece_length: int,
        total_length: int,
        target_file_path: str,
    ):
        self.pieces = pieces
        self.downloaded_pieces: set[int] = set()
        self.available_pieces: set[int] = set(range(len(pieces)))
        self.total_pieces = len(pieces)
        self.file_manager = file_manager
        self.piece_length = piece_length
        self.total_length = total_length
        self.target_file_path = target_file_path
        self._piece_progress: dict[int, int] = {}
        self._last_reported_percent = -1
        self._lock = asyncio.Lock()

    def register_piece_hash(self, index: int, piece_hash: bytes) -> None:
        self.pieces[index] = piece_hash

    def _piece_size(self, index: int) -> int:
        if self.total_pieces == 0:
            return 0
        if index < self.total_pieces - 1:
            return self.piece_length
        consumed = self.piece_length * (self.total_pieces - 1)
        return max(0, self.total_length - consumed)

    def get_piece_size(self, index: int) -> int:
        return self._piece_size(index)

    def register_piece(
        self,
        index: int,
        data: bytes,
        offset: int = 0,
        file_path: str | None = None,
    ) -> bool:
        if index < 0 or index >= self.total_pieces:
            raise IndexError(f"Invalid piece index: {index}")

        piece_size = self._piece_size(index)
        if offset < 0 or offset >= piece_size:
            raise ValueError(f"Invalid offset {offset} for piece {index}")
        if offset + len(data) > piece_size:
            raise ValueError(
                f"Block exceeds piece bounds: piece={index}, offset={offset}, block={len(data)}, piece_size={piece_size}"
            )

        target_path = file_path or self.target_file_path
        self.file_manager.save_piece(
            piece_index=index,
            data=data,
            file_path=target_path,
            piece_length=self.piece_length,
            offset=offset,
        )

        if index in self.downloaded_pieces:
            return True

        progress = self._piece_progress.get(index, 0) + len(data)
        self._piece_progress[index] = min(piece_size, progress)

        if piece_size > 0 and self._piece_progress[index] >= piece_size:
            self.mark_piece_downloaded(index)
            return True

        return False

    def get_piece(self, index: int) -> bytes:
        return self.pieces[index]

    async def find_next_piece(self, peer_bitfield: bytes) -> int | None:
        async with self._lock:
            for idx in range(self.total_pieces):
                if idx in self.downloaded_pieces or idx not in self.available_pieces:
                    continue

                byte_index = idx // 8
                bit_index = idx % 8

                if byte_index >= len(peer_bitfield):
                    continue

                has_piece = (peer_bitfield[byte_index] >> (7 - bit_index)) & 1

                if has_piece:
                    self.available_pieces.discard(idx)
                    return idx

        return None

    def mark_piece_downloaded(self, index: int) -> None:
        if index in self.downloaded_pieces:
            return

        self.downloaded_pieces.add(index)

        if self.total_pieces == 0:
            return

        progress = (len(self.downloaded_pieces) / self.total_pieces) * 100
        progress_int = int(progress)
        if progress_int > self._last_reported_percent:
            self._last_reported_percent = progress_int
            logger.info(
                f"[PROGRESS] {progress:.2f}% ({len(self.downloaded_pieces)}/{self.total_pieces} pieces)"
            )

    def mark_piece_available(self, index: int) -> None:
        self.available_pieces.add(index)

    def mark_piece_unavailable(self, index: int) -> None:
        self.available_pieces.discard(index)

    def is_complete(self) -> bool:
        return len(self.downloaded_pieces) == self.total_pieces

    def get_downloaded_bytes(self) -> int:
        return sum(self._piece_progress.values())

    def get_progress_percent(self) -> float:
        if self.total_length <= 0:
            return 0.0
        return min(100.0, (self.get_downloaded_bytes() / self.total_length) * 100)
