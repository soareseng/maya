import pytest

from src.magnet.magnet import parse_magnet_link


def test_parse_magnet_link():
    magnet_link = "magnet:?xt=urn:btih:6f5680d2861d631a7c9621ccac1d893018605274&dn=pop-os_24.04_amd64_generic_24.iso&tr=udp%3A%2F%2Ffosstorrents.com%3A6969%2Fannounce&tr=http%3A%2F%2Ffosstorrents.com%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce&tr=udp%3A%2F%2Ftracker-udp.gbitt.info%3A80%2Fannounce&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.theoks.net%3A6969%2Fannounce&tr=udp%3A%2F%2Fopentracker.io%3A6969%2Fannounce&ws=https%3A%2F%2Fiso.pop-os.org%2F24.04%2Famd64%2Fgeneric%2F24%2Fpop-os_24.04_amd64_generic_24.iso&ws=http%3A%2F%2Ffosstorrents.com%2Fdirect-links%2Fpop-os_24.04_amd64_generic_24.iso"
    magnet = parse_magnet_link(magnet_link)
    assert magnet.name == "pop-os_24.04_amd64_generic_24.iso"
    assert magnet.info_hash == "6f5680d2861d631a7c9621ccac1d893018605274"
    assert len(magnet.info_hash) == 40
    assert magnet.tracker_urls == [
        "udp://fosstorrents.com:6969/announce",
        "http://fosstorrents.com:6969/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://tracker-udp.gbitt.info:80/announce",
        "udp://open.demonii.com:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.theoks.net:6969/announce",
        "udp://opentracker.io:6969/announce",
    ]


def test_parse_magnet_link_invalid_info_hash():
    magnet_link = "magnet:?xt=urn:btih:invalid_info_hash&dn=example_file.txt&tr=udp%3A%2F%2Ftracker.example.com%3A6969%2Fannounce"
    with pytest.raises(
        ValueError, match="Invalid info hash length: 17. Expected 40 characters."
    ):
        parse_magnet_link(magnet_link)


def test_parse_magnet_link_missing_query():
    magnet_link = "magnet:invalid_link"
    with pytest.raises(
        ValueError, match="Invalid magnet link: missing query parameters."
    ):
        parse_magnet_link(magnet_link)
