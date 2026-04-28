from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.encoder.bencoder import Encoder
from src.torrent import Torrent
from src.utils.hash import sha1_encode


def _write_torrent_file(path: Path, torrent_data: dict) -> None:
    path.write_bytes(Encoder().encode(torrent_data))


def _build_single_file_torrent(path: Path) -> Path:
    info = {
        b"name": b"sample.txt",
        b"piece length": 4,
        b"pieces": b"a" * 20 + b"b" * 20,
        b"length": 6,
    }
    torrent_data = {b"announce": b"http://tracker.local/announce", b"info": info}
    torrent_path = path / "sample.torrent"
    _write_torrent_file(torrent_path, torrent_data)
    return torrent_path


def _build_multi_file_torrent(path: Path) -> Path:
    info = {
        b"name": b"bundle",
        b"piece length": 4,
        b"pieces": b"a" * 20,
        b"files": [
            {b"length": 2, b"path": [b"dir", b"one.bin"]},
            {b"length": 3, b"path": [b"dir", b"two.bin"]},
        ],
    }
    torrent_data = {
        b"announce-list": [[b"udp://tracker.local:80/announce"]],
        b"info": info,
    }
    torrent_path = path / "bundle.torrent"
    _write_torrent_file(torrent_path, torrent_data)
    return torrent_path


def test_setters_and_error_paths() -> None:
    torrent = Torrent()

    torrent._set_announce_list({b"announce": b"http://tracker.local/announce"})
    assert torrent.announce_list == ["http://tracker.local/announce"]

    torrent._set_announce_list({b"announce-list": [[b"http://a"], [b"udp://b"]]})
    assert torrent.announce_list == ["http://a", "udp://b"]

    with pytest.raises(ValueError, match="missing announce URL"):
        torrent._set_announce_list({})

    with pytest.raises(ValueError, match="name is not valid UTF-8"):
        torrent._set_name({b"name": b"\xff"})


def test_files_length_piece_data_and_info_hash() -> None:
    torrent = Torrent()
    torrent.name = "sample"

    torrent._set_files_and_length({b"length": 6})
    assert torrent.length == 6
    assert torrent.files == [{"path": "sample", "length": 6}]

    torrent._set_files_and_length(
        {
            b"files": [
                {b"length": 2, b"path": [b"dir", b"one.bin"]},
                {b"length": 3, b"path": [b"dir", b"two.bin"]},
            ]
        }
    )
    assert torrent.length == 5
    assert torrent.files == [
        {"path": str(Path("sample", "dir", "one.bin")), "length": 2},
        {"path": str(Path("sample", "dir", "two.bin")), "length": 3},
    ]

    torrent._set_piece_data({b"piece length": 4, b"pieces": b"a" * 40})
    assert torrent.piece_length == 4
    assert torrent.pieces == b"a" * 40
    assert torrent.number_of_pieces == 2

    info = {b"name": b"sample", b"piece length": 4, b"pieces": b"a" * 40, b"length": 6}
    torrent._set_info_hash(info)
    assert torrent.info_hash == sha1_encode(Encoder().encode(info))


def test_resolve_torrent_path_supports_test_and_tests_paths() -> None:
    torrent = Torrent()

    resolved = torrent._resolve_torrent_path("test/files/ubuntu.torrent")
    assert resolved.name == "ubuntu.torrent"
    assert resolved.exists()

    resolved = torrent._resolve_torrent_path("tests/files/ubuntu.torrent")
    assert resolved.name == "ubuntu.torrent"
    assert resolved.exists()

    with pytest.raises(FileNotFoundError):
        torrent._resolve_torrent_path("missing/file.torrent")


def test_extract_and_parse_peers() -> None:
    torrent = Torrent(number_of_pieces=8)

    peers = torrent._extract_peers_from_response(
        {
            "peers": b"\x7f\x00\x00\x01\x1a\xe1\x7f\x00\x00\x02\x1b\x58\x00",
            "interval": 12,
            "complete": 2,
            "incomplete": 3,
        }
    )
    assert [(peer.ip, peer.port) for peer in peers] == [
        ("127.0.0.1", 6881),
        ("127.0.0.2", 7000),
    ]

    peers = torrent._extract_peers_from_response(
        {
            "peers": [
                {"ip": b"127.0.0.3", "port": 6882},
                {"ip": "127.0.0.4", "port": 6883},
            ],
            "interval": 12,
            "complete": 2,
            "incomplete": 3,
        }
    )
    assert [(peer.ip, peer.port) for peer in peers] == [
        ("127.0.0.3", 6882),
        ("127.0.0.4", 6883),
    ]

    assert [
        (peer.ip, peer.port)
        for peer in torrent._parse_peers(b"\x7f\x00\x00\x01\x1a\xe1\x00")
    ] == [("127.0.0.1", 6881)]


def test_get_tracker_client_and_missing_trackers() -> None:
    torrent = Torrent()

    assert torrent._get_tracker_client("http://example") is torrent.http_tracker
    assert torrent._get_tracker_client("https://example") is torrent.http_tracker
    assert torrent._get_tracker_client("udp://example") is torrent.udp_tracker

    with pytest.raises(ValueError, match="Unsupported tracker scheme"):
        torrent._get_tracker_client("ftp://example")


def test_load_from_path_with_small_torrent(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    torrent_path = _build_single_file_torrent(tmp_path)

    torrent = Torrent()
    torrent.load_from_path(str(torrent_path))

    assert torrent.announce_list == ["http://tracker.local/announce"]
    assert torrent.name == "sample.txt"
    assert torrent.length == 6
    assert torrent.piece_length == 4
    assert torrent.number_of_pieces == 2
    assert torrent.files == [{"path": "sample.txt", "length": 6}]
    assert torrent.piece_manager is not None
    assert torrent.piece_manager.total_pieces == 2
    assert (tmp_path / "downloads" / "sample.txt").exists()


def test_load_from_path_supports_multi_file_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    torrent_path = _build_multi_file_torrent(tmp_path)

    torrent = Torrent()
    torrent.load_from_path(str(torrent_path))

    assert torrent.name == "bundle"
    assert torrent.files == [
        {"path": str(Path("bundle", "dir", "one.bin")), "length": 2},
        {"path": str(Path("bundle", "dir", "two.bin")), "length": 3},
    ]


def test_announce_and_connect_to_peers() -> None:
    torrent = Torrent(
        announce_list=["http://tracker.local/announce"], length=10, number_of_pieces=2
    )
    torrent.info_hash = b"x" * 20
    torrent.peer_manager = torrent.peer_manager.__class__()
    torrent.piece_manager = SimpleNamespace()

    class FakeTracker:
        def announce(self, *args, **kwargs):
            return {
                "peers": b"\x7f\x00\x00\x01\x1a\xe1",
                "interval": 30,
                "complete": 1,
                "incomplete": 0,
            }

    torrent.http_tracker = FakeTracker()
    torrent._get_tracker_client = Mock(return_value=torrent.http_tracker)

    assert asyncio.run(torrent.announce()) is True
    assert torrent.peer_manager.peer_count() == 1
    assert torrent.last_announce_ok == 1
    assert torrent.last_announce_total == 1
    assert torrent.last_announce_new_peers == 1

    torrent.peer_manager = SimpleNamespace(
        peer_count=Mock(return_value=0), connect_new_peers=AsyncMock(return_value=0)
    )
    asyncio.run(torrent.connect_to_peers())
    torrent.peer_manager.connect_new_peers.assert_not_awaited()


def test_run_stops_when_piece_manager_completes(monkeypatch) -> None:
    torrent = Torrent(
        announce_list=["http://tracker.local/announce"], name="sample.txt"
    )
    torrent.peer_manager.shutdown = AsyncMock()
    torrent.file_manager.close_all = Mock()
    torrent.announce = AsyncMock(return_value=True)
    torrent.connect_to_peers = AsyncMock()
    torrent.piece_manager = SimpleNamespace(is_complete=lambda: True)

    monkeypatch.setattr("src.torrent.asyncio.sleep", AsyncMock())

    asyncio.run(torrent.run())

    torrent.announce.assert_awaited_once()
    torrent.connect_to_peers.assert_awaited_once()
    torrent.peer_manager.shutdown.assert_awaited_once()
    torrent.file_manager.close_all.assert_called_once()
