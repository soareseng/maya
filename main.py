async def main():
    from src.torrent import Torrent
    from src.ui.orange_black_tui import run_torrent_with_tui

    torrent = Torrent()
    torrent.load_from_path("tests/files/ubuntu.torrent")
    await run_torrent_with_tui(torrent)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
