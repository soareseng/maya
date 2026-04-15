from enum import Enum


class Message(Enum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8


class Peer:
    def __init__(self, peer_id):
        pass

    def send_message(self, message: Message, payload: bytes = b""):
        pass

    def receive_message(self) -> tuple[Message, bytes]:
        pass
