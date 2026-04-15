class PieceManager:
    def __init__(self, pieces: list[bytes]):
        self.pieces = pieces

    def register_piece(self, index: int, data: bytes):
        self.pieces[index] = data

    def get_piece(self, index: int) -> bytes:
        return self.pieces[index]
