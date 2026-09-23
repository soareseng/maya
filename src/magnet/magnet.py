from dataclasses import dataclass

from typing import Optional
from urllib.parse import unquote


@dataclass
class MagnetInfo:
    name: Optional[str] = None
    tracker_urls: Optional[list[str]] = None
    info_hash: Optional[str] = None


def parse_magnet_link(magnet_link: str) -> MagnetInfo:
    """
    Parses a magnet link and extracts the name, tracker URLs, and info hash.

    Args:
        magnet_link (str): The magnet link to parse.
    Returns:
        MagnetInfo: An object containing the name, tracker URLs, and info hash.
    """
    if "?" not in magnet_link:
        raise ValueError(
            "[MAGNET] Invalid magnet link: missing query parameters.")
    params = magnet_link.split("?", 1)[1].split("&")
    info_hash = None
    name = None
    tracker_urls = []
    for param in params:
        if param.startswith("xt=urn:btih:"):
            info_hash = param.split("xt=urn:btih:")
            info_hash = info_hash[1]
            if len(info_hash) != 40:
                raise ValueError(
                    f"[MAGNET] Invalid info hash length: {
                        len(info_hash)}. Expected 40 characters."
                )
        elif param.startswith("dn="):
            name = param.split("dn=")
            if len(name) < 1:
                raise ValueError(
                    f"[MAGNET] Invalid name {name}. Expected len(name) >= 1")
            name = unquote(name[1])

        elif param.startswith("tr="):
            tracker_url = param.split("tr=", 1)
            if len(tracker_url) < 1:
                raise ValueError(f"[MAGNET] Invalid tracker url {
                                 tracker_url}. Expected len(tracker_url) >= 1")
            tracker_url = unquote(tracker_url[1])
            tracker_urls.append(tracker_url)
    return MagnetInfo(name=name, tracker_urls=tracker_urls, info_hash=info_hash)
