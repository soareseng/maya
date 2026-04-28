from typing import Union, List, Dict
from typing_extensions import TypeAlias

BencodeValue: TypeAlias = Union[
    int, bytes, List["BencodeValue"], Dict[bytes, "BencodeValue"]
]


class BencodeEncodeError(Exception):
    pass


class BencodeDecodeError(Exception):
    pass


class Encoder:
    def encode(self, value: BencodeValue) -> bytes:
        if isinstance(value, bool):
            raise BencodeEncodeError(f"Unsupported type: {type(value)}")

        if isinstance(value, int):
            return f"i{value}e".encode()

        if isinstance(value, str):
            encoded: bytes = value.encode()
            return f"{len(encoded)}:".encode() + encoded

        if isinstance(value, bytes):
            return f"{len(value)}:".encode() + value

        if isinstance(value, list):
            encoded_list: List[bytes] = [self.encode(v) for v in value]
            return b"l" + b"".join(encoded_list) + b"e"

        if isinstance(value, dict):
            encoded_items: List[bytes] = []
            for key in sorted(value.keys()):
                if not isinstance(key, (str, bytes)):
                    raise BencodeEncodeError("Dictionary keys must be str or bytes")
                encoded_key: bytes = self.encode(key)
                encoded_value: bytes = self.encode(value[key])
                encoded_items.append(encoded_key + encoded_value)
            return b"d" + b"".join(encoded_items) + b"e"

        raise BencodeEncodeError(f"Unsupported type: {type(value)}")


class Decoder:
    data: bytes
    index: int
    length: int

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise BencodeDecodeError("Input must be bytes")
        self.data = data
        self.index = 0
        self.length = len(data)

    def decode(self) -> BencodeValue:
        if self.index >= self.length:
            raise BencodeDecodeError("Unexpected end of input")

        char: bytes = self.data[self.index : self.index + 1]

        if char == b"i":
            return self._decode_int()

        if char == b"l":
            return self._decode_list()

        if char == b"d":
            return self._decode_dict()

        if char.isdigit():
            return self._decode_bytes()

        raise BencodeDecodeError(f"Invalid token at index {self.index}")

    def _decode_int(self) -> int:
        self.index += 1
        end = self.data.find(b"e", self.index)
        if end == -1:
            raise BencodeDecodeError("Integer not terminated with 'e'")
        number_bytes: bytes = self.data[self.index : end]
        try:
            number = int(number_bytes)
        except ValueError:
            raise BencodeDecodeError("Invalid integer value")
        self.index = end + 1
        return number

    def _decode_bytes(self) -> bytes:
        colon = self.data.find(b":", self.index)
        if colon == -1:
            raise BencodeDecodeError("Invalid string: missing ':'")
        length_part: bytes = self.data[self.index : colon]
        if not length_part.isdigit():
            raise BencodeDecodeError("Invalid string length")
        str_len = int(length_part)
        start = colon + 1
        end = start + str_len
        if end > self.length:
            raise BencodeDecodeError("String length exceeds input size")
        result: bytes = self.data[start:end]
        self.index = end
        return result

    def _decode_list(self) -> List[BencodeValue]:
        self.index += 1
        result: List[BencodeValue] = []
        while True:
            if self.index >= self.length:
                raise BencodeDecodeError("List not terminated")
            if self.data[self.index : self.index + 1] == b"e":
                self.index += 1
                break
            result.append(self.decode())
        return result

    def _decode_dict(self) -> Dict[bytes, BencodeValue]:
        self.index += 1
        result: Dict[bytes, BencodeValue] = {}
        while True:
            if self.index >= self.length:
                raise BencodeDecodeError("Dict not terminated")
            if self.data[self.index : self.index + 1] == b"e":
                self.index += 1
                break
            key: BencodeValue = self.decode()
            if not isinstance(key, bytes):
                raise BencodeDecodeError("Dictionary keys must be bytes")
            value: BencodeValue = self.decode()
            result[key] = value
        return result
