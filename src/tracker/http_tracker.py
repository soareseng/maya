from typing import Any

import requests

from bencoder.src.bencoder import Decoder
from src.tracker.tracker import Tracker
from src.utils.logger import logger


class HTTPTracker(Tracker):
    def __init__(self):
        self.session = requests.Session()

    def _parse_response(self, data: bytes) -> dict[str, Any]:
        decoder = Decoder(data)
        decoded = decoder.decode()

        return {
            "peers": decoded.get(b"peers", b""),
            "interval": decoded.get(b"interval", 1800),
            "complete": decoded.get(b"complete", 0),
            "incomplete": decoded.get(b"incomplete", 0),
        }

    def announce(
        self,
        url: str,
        info_hash: bytes,
        peer_id: bytes,
        port: int,
        uploaded: int = 0,
        downloaded: int = 0,
        left: int = 0,
        numwant: int = 50,
    ) -> dict[str, Any]:
        params = {
            "info_hash": info_hash,
            "peer_id": peer_id,
            "port": port,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "left": left,
            "compact": 1,
            "numwant": numwant,
            "event": "started",
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return self._parse_response(response.content)
        except requests.RequestException as e:
            logger.error(f"Error connecting to tracker: {e}")
            raise ConnectionError(f"Error connecting to tracker: {e}") from e
