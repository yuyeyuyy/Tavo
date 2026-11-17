import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.credentials.contracts import ProtectedHeader, PublicJWK


def encode(value: bytes):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: str, size=None, maximum=8192):
    data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if encode(data) != value or len(data) > maximum or (size is not None and len(data) != size):
        raise ValueError("invalid envelope")
    return data


def generate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def public_jwk(private):
    number = private.public_key().public_numbers().n
    return PublicJWK(n=encode(number.to_bytes(384, "big")))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate header")
        result[key] = value
    return result


def decrypt(private, envelope: str, expected: ProtectedHeader):
    """Strict JWE Compact subset: RSA-OAEP-256 / A256GCM, no negotiated algorithms."""
    protected, wrapped, iv, ciphertext, tag = envelope.split(".")
    raw = json.loads(decode(protected, maximum=1024), object_pairs_hook=unique_object)
    parsed = ProtectedHeader.model_validate(raw)
    if parsed != expected or raw != expected.model_dump(by_alias=True) or type(raw["v"]) is not int:
        raise ValueError("invalid envelope")
    key = bytearray(
        private.decrypt(
            decode(wrapped, size=384),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
    )
    plain = bytearray()
    try:
        if len(key) != 32:
            raise ValueError("invalid envelope")
        plain.extend(
            AESGCM(bytes(key)).decrypt(
                decode(iv, size=12),
                decode(ciphertext) + decode(tag, size=16),
                protected.encode("ascii"),
            )
        )
        if not 1 <= len(plain) <= 8192:
            raise ValueError("invalid envelope")
        return SecretStr(plain.decode("utf-8"))
    finally:
        key[:] = b"\0" * len(key)
        plain[:] = b"\0" * len(plain)
