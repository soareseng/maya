import requests
from typing import Dict
from src.tracker.tracker import Tracker


class HTTPTracker(Tracker):
    def __init__(self):
        self.session = requests.Session()

    def announce(
        self,
        url: str,
        info_hash: bytes,
        peer_id: bytes,
        port: int,
        uploaded: int = 0,
        downloaded: int = 0,
        left: int = 0,
    ) -> Dict:
        params = {
            "info_hash": info_hash,
            "peer_id": peer_id,
            "port": port,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "left": left,
            "compact": 1,
            "event": "started",
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return self._parse_response(response.content)
        except requests.RequestException as e:
            raise Exception(f"Erro ao conectar ao tracker: {e}")

    def _parse_response(self, data: bytes) -> Dict:
        from bencoder.src.bencoder import Bencoder

        decoder = Bencoder()
        decoded = decoder.decode(data)

        return {
            "peers": decoded.get(b"peers", b""),
            "interval": decoded.get(b"interval", 1800),
            "complete": decoded.get(b"complete", 0),
            "incomplete": decoded.get(b"incomplete", 0),
        }
