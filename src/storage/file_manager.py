from pathlib import Path
from typing import BinaryIO

from src.utils.logger import logger


class FileManager:
    def __init__(self):
        self.default_directory = "downloads"
        self._open_files: dict[str, BinaryIO] = {}

    def _get_file_handle(self, file_path: str) -> BinaryIO:
        full_path = Path(self.default_directory) / file_path
        self._ensure_directory_path(full_path.parent)
        key = str(full_path)

        handle = self._open_files.get(key)
        if handle is None or handle.closed:
            if not full_path.exists():
                open(full_path, "wb").close()

            handle = open(full_path, "r+b")
            self._open_files[key] = handle

        return handle

    def _ensure_directory_path(self, directory: Path) -> None:
        if not directory.parts:
            return

        current = Path(directory.root) if directory.is_absolute() else Path()
        for part in directory.parts:
            if part in {"", "/"}:
                continue

            current = current / part

            if current.exists() and current.is_file():
                current.unlink()

            if not current.exists():
                current.mkdir()

    def save_piece(
        self,
        piece_index: int,
        data: bytes,
        file_path: str,
        piece_length: int,
        offset: int = 0,
    ) -> None:
        logger.debug(
            f"Saving piece {piece_index} to {file_path} at offset {offset} with length {len(data)}"
        )
        f = self._get_file_handle(file_path)
        f.seek(piece_index * piece_length + offset)
        f.write(data)

    def save_piece_to_files(
        self,
        piece_index: int,
        data: bytes,
        piece_length: int,
        files: list[dict],
        offset: int = 0,
    ) -> None:
        absolute_offset = piece_index * piece_length + offset
        remaining_start = absolute_offset
        data_offset = 0
        cumulative = 0

        for file_info in files:
            file_path = str(file_info["path"])
            file_length = int(file_info["length"])
            file_start = cumulative
            file_end = file_start + file_length
            cumulative = file_end

            if remaining_start >= file_end:
                continue

            write_start = max(0, remaining_start - file_start)
            writable = file_end - (file_start + write_start)
            chunk_len = min(writable, len(data) - data_offset)

            if chunk_len <= 0:
                break

            handle = self._get_file_handle(file_path)
            handle.seek(write_start)
            handle.write(data[data_offset : data_offset + chunk_len])

            data_offset += chunk_len
            remaining_start += chunk_len

            if data_offset >= len(data):
                return

        if data_offset < len(data):
            raise ValueError("Piece data exceeds declared torrent file layout")

    def read_piece(
        self,
        piece_index: int,
        length: int,
        file_path: str,
        piece_length: int,
        offset: int = 0,
    ) -> bytes:
        logger.debug(
            f"Reading piece {piece_index} from {file_path} at offset {offset} with length {length}"
        )
        full_path = Path(self.default_directory) / file_path
        with open(full_path, "rb") as f:
            f.seek(piece_index * piece_length + offset)
            return f.read(length)

    def preallocate_file(self, file_path: str, length: int) -> None:
        full_path = Path(self.default_directory) / file_path
        self._ensure_directory_path(full_path.parent)
        with open(full_path, "wb") as f:
            f.truncate(length)

    def close_all(self) -> None:
        for handle in self._open_files.values():
            try:
                handle.close()
            except Exception:
                pass
        self._open_files.clear()

    def __del__(self) -> None:
        self.close_all()
