import hashlib
import secrets

_N = 2 ** 14
_R = 8
_P = 1
_DKLEN = 64


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
    ).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    actual = hash_password(password, salt)
    return secrets.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)