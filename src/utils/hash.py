import hashlib


def sha1_encode(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()


def sha1_decode(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
