from dataclasses import dataclass

from urllib.parse import unquote


@dataclass
class MagnetInfo:
    name: str
    tracker_urls: list[str]
    info_hash: str


def parse_magnet_link(magnet_link: str) -> MagnetInfo:
    """
    Parses a magnet link and extracts the name, tracker URL, and info hash.

    Args:
        magnet_link (str): The magnet link to parse.
    Returns:
        MagnetInfo: An object containing the name, tracker URL, and info hash.
    """
    if "?" not in magnet_link:
        raise ValueError("Invalid magnet link: missing query parameters.")
    params = magnet_link.split("?", 1)[1].split("&")
    info_hash = None
    name = None
    tracker_urls = []
    for param in params:
        if param.startswith("xt=urn:btih:"):
            info_hash = param.split("xt=urn:btih:")[1]
            if len(info_hash) != 40:
                raise ValueError(
                    f"Invalid info hash length: {len(info_hash)}. Expected 40 characters."
                )
        elif param.startswith("dn="):
            name = unquote(param.split("dn=")[1])
        elif param.startswith("tr="):
            tracker_url = unquote(param.split("tr=", 1)[1])
            tracker_urls.append(tracker_url)
    return MagnetInfo(name=name, tracker_urls=tracker_urls, info_hash=info_hash)
