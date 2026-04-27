from types import SimpleNamespace

import asyncio

from src.ui.orange_black_tui import OrangeBlackTUI, UILogHandler, run_torrent_with_tui
from src.utils.logger import logger


class FakePeer:
    def __init__(self, connected: bool) -> None:
        self.tcp_protocol = SimpleNamespace(is_connected=connected)

    def __hash__(self) -> int:
        return id(self)


def _make_tui() -> OrangeBlackTUI:
    log_handler = UILogHandler(max_entries=3)
    torrent = SimpleNamespace(
        name="sample.txt",
        announce_list=["http://tracker.local/announce"],
        peer_manager=SimpleNamespace(get_peers=lambda: {FakePeer(True), FakePeer(False)}),
        last_announce_ok=2,
        last_announce_total=3,
        last_announce_new_peers=1,
        piece_manager=SimpleNamespace(
            downloaded={0},
            total_pieces=4,
            get_downloaded_bytes=lambda: 4,
        ),
    )
    return OrangeBlackTUI(torrent=torrent, log_handler=log_handler)


def test_log_parsing_and_format_helpers() -> None:
    ui = _make_tui()
    ui.log_handler.entries.extend(
        [
            ("INFO", "Starting download: sample.txt"),
            ("INFO", "[PROGRESS] 25.00% (1/4 pieces)"),
            ("INFO", "trackers OK: 2/3"),
            ("INFO", "Connecting to peer: 127.0.0.1:6881"),
        ]
    )

    ui._parse_logs()
    assert ui.state.status == "Connecting"
    assert ui.state.progress_percent == 25.0
    assert ui.state.trackers_ok == 2
    assert ui.state.trackers_total == 3
    assert ui.state.peers_connected == 1
    assert ui.state.pieces_done == 1

    assert ui._format_bytes(1536) == "  1.50 KiB"
    assert ui._format_eta(65) == "00:01:05"
    assert ui._status_color() == "\033[38;5;221m"
    assert ui._visible_len("\033[31mred\033[0m") == 3
    assert ui._fit_visible_width("abc", 5).endswith("\033[0m")


def test_run_torrent_with_tui_restores_logger_handlers(monkeypatch) -> None:
    original_handlers = list(logger.handlers)

    class FakeUI:
        def __init__(self, torrent, log_handler):
            self.torrent = torrent
            self.log_handler = log_handler

        async def run(self, torrent_coro):
            await torrent_coro

    monkeypatch.setattr("src.ui.orange_black_tui.OrangeBlackTUI", FakeUI)

    class FakeTorrent:
        async def run(self):
            return None

    asyncio.run(run_torrent_with_tui(FakeTorrent()))

    assert logger.handlers == original_handlers