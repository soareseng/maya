import pytest
from typing import Any

from src.encoder.bencoder import (
    BencodeDecodeError,
    BencodeEncodeError,
    Decoder,
    Encoder,
    BencodeValue,
)

encode = Encoder().encode
decode = lambda data: Decoder(data).decode()


@pytest.mark.parametrize(
    "value, expected",
    [(123, b"i123e"), (-1, b"i-1e"), (0, b"i0e"), (999999999999, b"i999999999999e")],
)
def test_encode_int(value: int, expected: bytes):
    encoded = encode(value)
    assert encoded == expected
    decoded = decode(encoded)
    assert isinstance(decoded, int)
    assert decoded == value


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", b"0:"),
        ("a", b"1:a"),
        ("abc", b"3:abc"),
    ],
)
def test_encode_str(value: str, expected: bytes) -> None:
    encoded = encode(value)
    assert encoded == expected
    decoded = decode(encoded)
    assert isinstance(decoded, bytes)
    assert decoded == value.encode()


@pytest.mark.parametrize(
    "value, expected",
    [
        (b"", b"0:"),
        (b"abc", b"3:abc"),
    ],
)
def test_encode_bytes(value: bytes, expected: bytes) -> None:
    encoded = encode(value)
    assert encoded == expected
    decoded = decode(encoded)
    assert isinstance(decoded, bytes)
    assert decoded == value


@pytest.mark.parametrize(
    "value, expected",
    [
        ([], b"le"),
        (["abc3", 123], b"l4:abc3i123ee"),
        (["-1", -1, "ab", 714], b"l2:-1i-1e2:abi714ee"),
        ([1, [2, 3]], b"li1eli2ei3eee"),
    ],
)
def test_encode_list(value: list[BencodeValue], expected: bytes) -> None:
    encoded = encode(value)
    assert encoded == expected
    decoded = decode(encoded)
    assert isinstance(decoded, list)
    assert _check_bencode_structure(decoded, value)


def test_encode_empty_dict() -> None:
    value: dict[bytes, BencodeValue] = {}
    encoded = encode(value)
    assert encoded == b"de"
    decoded = decode(encoded)
    assert isinstance(decoded, dict)
    assert decoded == value


def test_encode_dict_sorted_keys() -> None:
    value: dict[str, BencodeValue] = {"b": 1, "a": 2}
    encoded = encode(value)
    assert encoded == b"d1:ai2e1:bi1ee"
    decoded = decode(encoded)
    assert isinstance(decoded, dict)
    assert decoded == {b"a": 2, b"b": 1}


def test_encode_nested_dict() -> None:
    value: dict[str, BencodeValue] = {"a": {"b": 1}}
    encoded = encode(value)
    assert encoded == b"d1:ad1:bi1eee"
    decoded = decode(encoded)
    assert isinstance(decoded, dict)
    assert isinstance(decoded[b"a"], dict)
    assert decoded[b"a"][b"b"] == 1


def test_encode_dict_invalid_key() -> None:
    with pytest.raises(BencodeEncodeError):
        encode({1: "invalid"})


@pytest.mark.parametrize(
    "value",
    [1.5, True, None, object()],
)
def test_encode_invalid_types(value: Any) -> None:
    with pytest.raises(BencodeEncodeError):
        encode(value)


@pytest.mark.parametrize(
    "value",
    [
        123,
        -1,
        0,
        "abc",
        "",
        [1, "a", [2, 3]],
        {"a": 1, "b": ["x", 2]},
    ],
)
def test_round_trip(value: BencodeValue) -> None:
    encoded = encode(value)
    decoded = decode(encoded)
    assert _check_bencode_structure(decoded, value)


@pytest.mark.parametrize(
    "invalid",
    [
        b"",
        b"i12",
        b"3abc",
        b"d1:a1:b",
        b"l1:a1:b",
        b"iabc e",
    ],
)
def test_decoder_invalid_inputs(invalid: bytes) -> None:
    with pytest.raises(BencodeDecodeError):
        decode(invalid)


def test_decode_multiple_values_should_fail() -> None:
    data = b"i1ei2e"
    decoder = Decoder(data)
    first = decoder.decode()
    assert first == 1
    assert decoder.index != decoder.length


def _check_bencode_structure(decoded: BencodeValue, original: BencodeValue) -> bool:
    if isinstance(original, str):
        return isinstance(decoded, bytes) and decoded == original.encode()
    if isinstance(original, int):
        return isinstance(decoded, int) and decoded == original
    if isinstance(original, list):
        if not isinstance(decoded, list) or len(decoded) != len(original):
            return False
        return all(_check_bencode_structure(d, o) for d, o in zip(decoded, original))
    if isinstance(original, dict):
        if not isinstance(decoded, dict) or len(decoded) != len(original):
            return False
        for k, v in original.items():
            key_bytes = k.encode() if isinstance(k, str) else k
            if key_bytes not in decoded:
                return False
            if not _check_bencode_structure(decoded[key_bytes], v):
                return False
        return True
    return False
