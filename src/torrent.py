from bencoder.src.bencoder import Decoder
import hashlib
from typing import List


class Torrent:
    def __init__(
        self,
        announce_list: List[str] = [],
        info_hash: bytes = b"",
        length: int = 0,
        number_of_pieces: int = 0,
        piece_length: int = 0,
        pieces: bytes = b"",
        name: str = "",
        files: List[dict] = None,
    ):
        self.announce_list = announce_list
        self.info_hash = info_hash
        self.length = length
        self.number_of_pieces = number_of_pieces
        self.piece_length = piece_length
        self.pieces = pieces
        self.pieces_size = len(pieces) // 20
        self.name = name
        self.files = files or []

    def load_from_path(self, file_path: str) -> "Torrent":
        with open(file_path, "rb") as f:
            data = f.read()

        decoder = Decoder(data)
        torrent_data = decoder.decode()

        info = torrent_data[b"info"]

        announce_list = torrent_data.get(b"announce-list")
        if announce_list:
            self.announce_list = [url.decode("utf-8") for sublist in announce_list for url in sublist]
        else:
            self.announce_list = [torrent_data[b"announce"].decode("utf-8")]

        self.name = info[b"name"].decode("utf-8")

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

        self.piece_length = info[b"piece length"]
        self.pieces = info[b"pieces"]
        self.number_of_pieces = len(self.pieces) // 20

        bencoded_info = self._bencode_info(info)
        self.info_hash = hashlib.sha1(bencoded_info).digest()

        self.pieces_size = len(self.pieces) // 20
        return self

    @staticmethod
    def _bencode_info(info_dict: dict) -> bytes:
        from bencoder.src.bencoder import Encoder
        encoder = Encoder()
        return encoder.encode(info_dict)


def decode_piece(piece: bytes) -> str:
    return hashlib.sha1(piece).hexdigest()