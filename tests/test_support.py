import hashlib
import logging

from src.peer.message import Message, MessageType
from src.utils.hash import sha1_decode, sha1_encode
from src.utils.logger import ColoredFormatter, logger


def test_sha1_helpers_match_hashlib() -> None:
    data = b"maya torrent"

    assert sha1_encode(data) == hashlib.sha1(data).digest()
    assert sha1_decode(data) == hashlib.sha1(data).hexdigest()


def test_message_serialization_round_trip() -> None:
    message = Message(msg_length=5, msg_type=MessageType.HAVE, payload=b"\x00\x00\x00\x01")

    assert message.to_bytes() == b"\x00\x00\x00\x05\x04\x00\x00\x00\x01"
    assert str(message) == "Message(length=5, type=MessageType.HAVE, payload=b'\\x00\\x00\\x00\\x01')"


def test_colored_formatter_restores_levelname() -> None:
    formatter = ColoredFormatter("%(levelname)s - %(message)s")
    record = logging.LogRecord("Maya", logging.INFO, __file__, 1, "hello", (), None)

    formatted = formatter.format(record)

    assert "\033[92mINFO\033[0m" in formatted
    assert record.levelname == "INFO"


def test_logger_is_configured() -> None:
    assert logger.name == "Maya"
    assert logger.handlers
    assert any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers)