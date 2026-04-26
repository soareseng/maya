from enum import Enum


class MessageType(Enum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8
    PORT = 9


class Message:
    def __init__(self, msg_length: int, msg_type: MessageType, payload: bytes | None):
        self.msg_length = msg_length
        self.msg_type = msg_type
        self.payload = payload

    def to_bytes(self) -> bytes:
        return (
            self.msg_length.to_bytes(4, "big")
            + bytes([self.msg_type.value])
            + (self.payload or b"")
        )

    def __str__(self):
        return f"Message(length={self.msg_length}, type={self.msg_type}, payload={self.payload})"
