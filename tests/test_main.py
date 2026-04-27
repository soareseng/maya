from unittest.mock import AsyncMock

import asyncio

import main


def test_main_loads_expected_torrent_and_starts_ui(monkeypatch) -> None:
    class FakeTorrent:
        def __init__(self):
            self.loaded_path = None

        def load_from_path(self, file_path: str) -> None:
            self.loaded_path = file_path

        async def run(self):
            return None

    fake_torrent = FakeTorrent()

    monkeypatch.setattr("src.torrent.Torrent", lambda: fake_torrent)
    run_ui = AsyncMock(return_value=None)
    monkeypatch.setattr("src.ui.orange_black_tui.run_torrent_with_tui", run_ui)

    asyncio.run(main.main())

    assert fake_torrent.loaded_path == "tests/files/popos.torrent"
    run_ui.assert_awaited_once_with(fake_torrent)