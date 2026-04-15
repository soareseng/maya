import random
from typing import Optional

from bencoder.src.bencoder import Encoder, Decoder
from src.piece.piece_manager import PieceManager
from src.tracker.http_tracker import HTTPTracker
from src.utils.hash import sha1_encode


class Torrent:
    PORT = 6881
    PEER_ID_PREFIX = b"-MA0001-"

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
        self.piece_manager: Optional[PieceManager] = None
        self.peer_id = self._generate_peer_id()
        self.tracker = HTTPTracker()
        self.connected_peers: list[dict] = []

    def _set_name(self, info: dict):
        try:
            self.name = info[b"name"].decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Invalid torrent file: name is not valid UTF-8")

    def _set_announce_list(self, torrent_data: dict) -> list[str]:
        announce_list = torrent_data.get(b"announce-list")
        if announce_list:
            self.announce_list = [
                url.decode("utf-8") for sublist in announce_list for url in sublist
            ]
        else:
            self.announce_list = [torrent_data[b"announce"].decode("utf-8")]

    def _set_files_and_length(self, info: dict):
        if b"length" in info:
            self.length = info[b"length"]
            self.files = [{"path": self.name, "length": self.length}]
        elif b"files" in info:
            self.files = []
            self.length = 0
            for f in info[b"files"]:
                path = "/".join([p.decode("utf-8") for p in f[b"path"]])
                length = f[b"length"]
                self.files.append({"path": path, "length": length})
                self.length += length

    def _set_piece_data(self, info: dict):
        self.piece_length = info[b"piece length"]
        self.pieces = info[b"pieces"]
        self.number_of_pieces = len(self.pieces) // 20

    def _set_info_hash(self, info: dict):
        encoder = Encoder()
        bencoded_info = encoder.encode(info)
        self.info_hash = sha1_encode(bencoded_info)

    def _register_pieces(self, piece_manager):
        for i in range(self.number_of_pieces):
            print(f"Registering piece {i + 1}/{self.number_of_pieces}")
            print(f"Piece hash: {self.pieces[i * 20:(i + 1) * 20].hex()}")
            print("")
            piece_hash = self.pieces[i * 20 : (i + 1) * 20]
            piece_manager.register_piece(i, piece_hash)

    def load_from_path(self, file_path: str):
        with open(file_path, "rb") as f:
            data = f.read()

        decoder = Decoder(data)
        torrent_data = decoder.decode()

        info = torrent_data[b"info"]
        self._set_announce_list(torrent_data)
        self._set_name(info)
        self._set_files_and_length(info)
        self._set_piece_data(info)
        self._set_info_hash(info)

        self.piece_manager = PieceManager([b""] * self.number_of_pieces)
        self._register_pieces(self.piece_manager)

    def _generate_peer_id(self) -> bytes:
        random_part = bytes([random.randint(0, 255) for _ in range(12)])
        return self.PEER_ID_PREFIX + random_part

    async def announce(self, uploaded: int = 0, downloaded: int = 0) -> bool:
        if not self.announce_list:
            print("Nenhum rastreador disponível")
            return False

        left = max(0, self.length - downloaded)

        for tracker_url in self.announce_list:
            try:
                response = self.tracker.announce(
                    url=tracker_url,
                    info_hash=self.info_hash,
                    peer_id=self.peer_id,
                    port=self.PORT,
                    uploaded=uploaded,
                    downloaded=downloaded,
                    left=left,
                )
                self._process_tracker_response(response)
                print(f"Anúncio bem-sucedido: {tracker_url}")
                return True
            except Exception as e:
                print(f"Erro ao anunciar a {tracker_url}: {e}")
                continue

        print("Falha ao anunciar em todos os rastreadores")
        return False

    def _process_tracker_response(self, response: dict) -> None:
        peers_data = response.get("peers", b"")
        if isinstance(peers_data, bytes):
            self.connected_peers = self._parse_peers(peers_data)
        else:
            self.connected_peers = peers_data

        interval = response.get("interval", 1800)
        complete = response.get("complete", 0)
        incomplete = response.get("incomplete", 0)

        print(f"Peers encontrados: {len(self.connected_peers)}")
        print(f"Seeders: {complete} | Leechers: {incomplete}")
        print(f"Próximo anúncio em: {interval}s")

    @staticmethod
    def _parse_peers(peers_data: bytes) -> list[dict]:
        peers = []
        for i in range(0, len(peers_data), 6):
            if i + 6 > len(peers_data):
                break
            ip = ".".join(str(b) for b in peers_data[i : i + 4])
            port = int.from_bytes(peers_data[i + 4 : i + 6], "big")
            peers.append({"ip": ip, "port": port})
        return peers

    async def run(self) -> None:
        print(f"Iniciando download de: {self.name}")
        print(f"Tamanho total: {self.length} bytes")
        print(f"Número de pieces: {self.number_of_pieces}")
        print("---")

        await self.announce()
