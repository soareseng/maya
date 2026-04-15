from src.torrent import Torrent


def test_load_from_path():
    torrent = Torrent()
    torrent.load_from_path("test/files/ubuntu.torrent")
    assert torrent.announce_list == [
        "https://torrent.ubuntu.com/announce",
        "https://ipv6.torrent.ubuntu.com/announce",
    ]
    assert torrent.length == 5702520832
    assert torrent.number_of_pieces == 21754
    assert torrent.piece_length == 262144
    assert torrent.files == [
        {"path": "ubuntu-25.10-desktop-amd64.iso", "length": 5702520832}
    ]
    assert torrent.name == "ubuntu-25.10-desktop-amd64.iso"
