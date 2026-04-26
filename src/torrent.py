import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bencoder.src.bencoder import Decoder, Encoder
from src.peer.peer import Peer
from src.peer.peer_manager import PeerManager
from src.piece.piece_manager import PieceManager
from src.storage.file_manager import FileManager
from src.tracker.http_tracker import HTTPTracker
from src.tracker.udp_tracker import UDPTracker
from src.utils.hash import sha1_encode
from src.utils.logger import logger


class Torrent:
    PORT = 49152
    PEER_ID_PREFIX = b"-MA0001-"
    TRACKER_NUMWANT = 200
    MAX_PEERS = 300
    REANNOUNCE_INTERVAL_SECONDS = 15

    def __init__(
        self,
        announce_list: list[str] | None = None,
        info_hash: bytes = b"",
        length: int = 0,
        number_of_pieces: int = 0,
        piece_length: int = 0,
        pieces: bytes = b"",
        name: str = "",
        files: list[dict] | None = None,
    ):
        self.announce_list = announce_list or []
        self.info_hash = info_hash
        self.length = length
        self.number_of_pieces = number_of_pieces
        self.piece_length = piece_length
        self.pieces = pieces
        self.name = name
        self.files = files or []
        self.progress = 0.0
        self.peer_id = self._generate_peer_id()
        self.http_tracker = HTTPTracker()
        self.udp_tracker = UDPTracker()
        self.connected_peers: list[Peer] = []
        self.piece_manager: PieceManager | None = None
        self.file_manager = FileManager()
        self.peer_manager = PeerManager()
        self.last_announce_ok = 0
        self.last_announce_total = 0
        self.last_announce_new_peers = 0

    def _get_tracker_client(self, tracker_url: str) -> HTTPTracker | UDPTracker:
        scheme = urlparse(tracker_url).scheme.lower()
        if scheme == "udp":
            return self.udp_tracker
        if scheme in {"http", "https"}:
            return self.http_tracker
        raise ValueError(f"Unsupported tracker scheme: {scheme}")

    def _set_name(self, info: dict) -> None:
        try:
            self.name = info[b"name"].decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Invalid torrent file: name is not valid UTF-8")

    def _set_announce_list(self, torrent_data: dict[bytes, Any]) -> None:
        announce_list = torrent_data.get(b"announce-list")
        if announce_list:
            self.announce_list = [
                url.decode("utf-8") for sublist in announce_list for url in sublist
            ]
        else:
            announce = torrent_data.get(b"announce")
            if not announce:
                raise ValueError("Invalid torrent file: missing announce URL")
            self.announce_list = [announce.decode("utf-8")]

    def _set_files_and_length(self, info: dict[bytes, Any]) -> None:
        if b"length" in info:
            self.length = info[b"length"]
            self.files = [{"path": self.name, "length": self.length}]
        elif b"files" in info:
            self.files = []
            self.length = 0
            for f in info[b"files"]:
                path_parts = [p.decode("utf-8") for p in f[b"path"]]
                path = str(Path(self.name, *path_parts))
                length = f[b"length"]
                self.files.append({"path": path, "length": length})
                self.length += length

    def _set_piece_data(self, info: dict[bytes, Any]) -> None:
        self.piece_length = info[b"piece length"]
        self.pieces = info[b"pieces"]
        self.number_of_pieces = len(self.pieces) // 20

    def _set_info_hash(self, info: dict[bytes, Any]) -> None:
        encoder = Encoder()
        bencoded_info = encoder.encode(info)
        self.info_hash = sha1_encode(bencoded_info)

    def _register_pieces(self) -> None:
        self.piece_manager = PieceManager(
            pieces=[b""] * self.number_of_pieces,
            file_manager=self.file_manager,
            piece_length=self.piece_length,
            total_length=self.length,
            target_file_path=self.name,
            torrent=self,
            file_layout=self.files,
        )
        logger.info(f"Registering {self.number_of_pieces} pieces...")
        for i in range(self.number_of_pieces):
            piece_hash = self.pieces[i * 20 : (i + 1) * 20]
            self.piece_manager.register_piece_hash(i, piece_hash)
            if (i + 1) % 5000 == 0:
                logger.info(f"Pieces registered: {i + 1}/{self.number_of_pieces}")
        logger.info("Piece registration completed.")

    def _resolve_torrent_path(self, file_path: str) -> Path:
        candidate = Path(file_path)
        project_root = Path(__file__).resolve().parents[1]

        candidates = [candidate, project_root / candidate]
        if candidate.parts and candidate.parts[0] == "test":
            candidates.append(project_root / "tests" / Path(*candidate.parts[1:]))

        for resolved in candidates:
            if resolved.exists():
                return resolved
        raise FileNotFoundError(f"Torrent file not found: {file_path}")

    def _preallocate_files(self) -> None:
        for file in self.files:
            self.file_manager.preallocate_file(file["path"], file["length"])

    def load_from_path(self, file_path: str) -> None:
        resolved_path = self._resolve_torrent_path(file_path)
        with open(resolved_path, "rb") as f:
            data = f.read()

        decoder = Decoder(data)
        torrent_data = decoder.decode()

        info = torrent_data[b"info"]
        self._set_announce_list(torrent_data)
        self._set_name(info)
        self._set_files_and_length(info)
        self._preallocate_files()
        self._set_piece_data(info)
        self._set_info_hash(info)
        self._register_pieces()

    def _generate_peer_id(self) -> bytes:
        random_part = os.urandom(12)
        return self.PEER_ID_PREFIX + random_part

    def _generate_remote_peer_id(self) -> bytes:
        return os.urandom(20)

    async def announce(self, uploaded: int = 0, downloaded: int = 0) -> bool:
        if not self.announce_list:
            logger.warning("No trackers available")
            return False

        left = max(0, self.length - downloaded)
        attempted_announces = len(self.announce_list)
        successful_announces = 0
        total_new_peers = 0

        async def announce_single_tracker(tracker_url: str) -> tuple[str, list[Peer] | None, Exception | None]:
            try:
                tracker_client = self._get_tracker_client(tracker_url)
                response = await asyncio.to_thread(
                    tracker_client.announce,
                    tracker_url,
                    self.info_hash,
                    self.peer_id,
                    self.PORT,
                    uploaded,
                    downloaded,
                    left,
                    self.TRACKER_NUMWANT,
                )
                return tracker_url, self._extract_peers_from_response(response), None
            except Exception as e:
                return tracker_url, None, e

        results = await asyncio.gather(
            *(announce_single_tracker(url) for url in self.announce_list),
            return_exceptions=False,
        )

        for tracker_url, tracker_peers, error in results:
            if error is not None:
                logger.error(f"Failed to announce to {tracker_url}: {error}")
                continue

            if tracker_peers is None:
                continue

            successful_announces += 1
            added_from_tracker = 0
            for peer in tracker_peers:
                if self.peer_manager.peer_count() >= self.MAX_PEERS:
                    break
                if self.peer_manager.add_peer(peer):
                    added_from_tracker += 1

            total_new_peers += added_from_tracker
            logger.info(
                f"Successful announce: {tracker_url} | peers received={len(tracker_peers)} | new={added_from_tracker}"
            )

        self.last_announce_ok = successful_announces
        self.last_announce_total = attempted_announces
        self.last_announce_new_peers = total_new_peers

        if self.peer_manager.peer_count() == 0:
            logger.error("Failed to get peers from all trackers")
            return False

        if total_new_peers > 0:
            logger.info(f"New peers discovered in this announce cycle: {total_new_peers}")

        return True

    def _extract_peers_from_response(self, response: dict[str, Any]) -> list[Peer]:
        peers_data = response.get("peers", b"")
        if isinstance(peers_data, bytes):
            peers = self._parse_peers(peers_data)
        elif isinstance(peers_data, list):
            normalized_peers: list[Peer] = []
            for peer_info in peers_data:
                ip = peer_info.get("ip")
                port = peer_info.get("port")
                if isinstance(ip, bytes):
                    ip = ip.decode("utf-8", errors="ignore")
                if ip and port:
                    normalized_peers.append(
                        Peer(
                            peer_id=self._generate_remote_peer_id(),
                            ip=ip,
                            port=int(port),
                            number_of_pieces=self.number_of_pieces,
                            piece_manager=self.piece_manager,
                        )
                    )
            peers = normalized_peers
        else:
            peers = []

        interval = response.get("interval", 1800)
        complete = response.get("complete", 0)
        incomplete = response.get("incomplete", 0)

        logger.info(f"Peers found in response: {len(peers)}")
        logger.info(f"Seeders: {complete} | Leechers: {incomplete}")
        logger.info(f"Next announce in: {interval}s")
        return peers

    def _parse_peers(self, peers_data: bytes) -> list[Peer]:
        peers: list[Peer] = []
        for i in range(0, len(peers_data), 6):
            if i + 6 > len(peers_data):
                break
            ip = ".".join(str(b) for b in peers_data[i : i + 4])
            port = int.from_bytes(peers_data[i + 4 : i + 6], "big")
            peers.append(
                Peer(
                    peer_id=self._generate_remote_peer_id(),
                    ip=ip,
                    port=port,
                    number_of_pieces=self.number_of_pieces,
                    piece_manager=self.piece_manager,
                )
            )
        return peers

    async def connect_to_peers(self) -> None:
        if self.peer_manager.peer_count() == 0:
            logger.warning("No peers available for connection")
            return

        started = await self.peer_manager.connect_new_peers(self.info_hash)
        if started:
            logger.info(f"Started connections for {started} new peer(s)")

    async def run(self) -> None:
        logger.info(f"Starting download: {self.name}")
        logger.info(f"Total size: {self.length} bytes")
        logger.info(f"Number of pieces: {self.number_of_pieces}")
        logger.info("---")

        try:
            announced = await self.announce()
            if announced:
                await self.connect_to_peers()

            while True:
                if self.piece_manager and self.piece_manager.is_complete():
                    logger.info("Download completed")
                    break

                await asyncio.sleep(self.REANNOUNCE_INTERVAL_SECONDS)

                announced = await self.announce()
                if announced:
                    await self.connect_to_peers()
        finally:
            await self.peer_manager.shutdown()
            self.file_manager.close_all()
