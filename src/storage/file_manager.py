from pathlib import Path
from typing import BinaryIO


class FileManager:
    def __init__(self):
        self.default_directory = "downloads"
        self._open_files: dict[str, BinaryIO] = {}

    def _get_file_handle(self, file_path: str) -> BinaryIO:
        full_path = Path(self.default_directory) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        key = str(full_path)

        handle = self._open_files.get(key)
        if handle is None or handle.closed:
            handle = open(full_path, "r+b")
            self._open_files[key] = handle
        return handle

    def save_piece(
        self,
        piece_index: int,
        data: bytes,
        file_path: str,
        piece_length: int,
        offset: int = 0,
    ) -> None:
        f = self._get_file_handle(file_path)
        f.seek(piece_index * piece_length + offset)
        f.write(data)

    def read_piece(
        self,
        piece_index: int,
        length: int,
        file_path: str,
        piece_length: int,
        offset: int = 0,
    ) -> bytes:
        full_path = Path(self.default_directory) / file_path
        with open(full_path, "rb") as f:
            f.seek(piece_index * piece_length + offset)
            return f.read(length)

    def preallocate_file(self, file_path: str, length: int) -> None:
        full_path = Path(self.default_directory) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
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
