import pytest

from src.storage.file_manager import FileManager


def test_preallocate_file_creates_target_and_directories(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = FileManager()

    manager.preallocate_file("nested/example.bin", 11)

    target = tmp_path / "downloads" / "nested" / "example.bin"
    assert target.exists()
    assert target.stat().st_size == 11


def test_save_piece_and_read_piece(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = FileManager()

    manager.save_piece(
        piece_index=1,
        data=b"abcd",
        file_path="chunks/data.bin",
        piece_length=4,
    )
    manager.close_all()

    assert (
        manager.read_piece(
            piece_index=1,
            length=4,
            file_path="chunks/data.bin",
            piece_length=4,
        )
        == b"abcd"
    )


def test_save_piece_to_files_spans_multiple_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    manager = FileManager()

    manager.save_piece_to_files(
        piece_index=0,
        data=b"ABCDEF",
        piece_length=4,
        files=[
            {"path": "multi/first.bin", "length": 4},
            {"path": "multi/second.bin", "length": 4},
        ],
        offset=2,
    )
    manager.close_all()

    assert (
        tmp_path / "downloads" / "multi" / "first.bin"
    ).read_bytes() == b"\x00\x00AB"
    assert (tmp_path / "downloads" / "multi" / "second.bin").read_bytes() == b"CDEF"


def test_save_piece_to_files_raises_when_layout_is_too_small(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = FileManager()

    with pytest.raises(
        ValueError, match="Piece data exceeds declared torrent file layout"
    ):
        manager.save_piece_to_files(
            piece_index=0,
            data=b"ABCDE",
            piece_length=4,
            files=[{"path": "tiny.bin", "length": 4}],
            offset=2,
        )


def test_ensure_directory_replaces_file_blocker(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    blocker = downloads / "blocked"
    blocker.write_text("block")

    manager = FileManager()
    manager.save_piece(
        piece_index=0,
        data=b"xy",
        file_path="blocked/output.bin",
        piece_length=2,
    )
    manager.close_all()

    assert blocker.is_dir()
    assert (blocker / "output.bin").read_bytes() == b"xy"
