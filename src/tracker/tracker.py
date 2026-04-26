from abc import ABC, abstractmethod
from typing import Any


class Tracker(ABC):
    @abstractmethod
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
        raise NotImplementedError
