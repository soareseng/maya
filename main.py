async def main():
    from src.torrent import Torrent

    torrent = Torrent()
    torrent.load_from_path("tests/files/ubuntu.torrent")
    await torrent.run()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
