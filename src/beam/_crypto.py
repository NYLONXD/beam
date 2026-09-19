"""Encrypting a file before it leaves the laptop, and decrypting it on arrival.

AES-256-GCM over 1 MiB chunks. Each chunk's nonce carries its index and a
"last chunk" flag, so reordered, dropped or truncated chunks fail to decrypt.
The key comes from the secret half of the code, which never reaches the host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ._protocol import BeamError

MAGIC = b"BEAM\x01"
CHUNK = 1 << 20


def _key(secret: str, salt: bytes) -> bytes:
    return hmac.new(secret.encode(), b"beam-v1" + salt, hashlib.sha256).digest()


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
    return prefix + struct.pack("!IB", index, int(last))


def encrypt_file(src, dst, secret: str, meta: dict, progress=None) -> None:
    """Write ``src`` to ``dst`` encrypted; ``meta`` (e.g. the filename) rides along."""
    salt, prefix = os.urandom(16), os.urandom(7)
    aes = AESGCM(_key(secret, salt))
    total = os.path.getsize(src)
    done = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC + salt + prefix)
        blob = aes.encrypt(_nonce(prefix, 0, False), json.dumps(meta).encode(), None)
        fout.write(struct.pack("!BI", 0, len(blob)) + blob)
        index = 1
        block = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK) if len(block) == CHUNK else b""
            last = not nxt
            blob = aes.encrypt(_nonce(prefix, index, last), block, None)
            fout.write(struct.pack("!BI", int(last), len(blob)) + blob)
            done += len(block)
            if progress:
                progress(done, total)
            if last:
                break
            block, index = nxt, index + 1


def decrypt_file(src, dst, secret: str) -> dict:
    """Decrypt ``src`` into ``dst``; return the metadata stored with it."""
    wrong = BeamError("could not decrypt: wrong code, or the file was damaged")
    with open(src, "rb") as fin:
        head = fin.read(len(MAGIC) + 16 + 7)
        if not head.startswith(MAGIC) or len(head) < len(MAGIC) + 23:
            raise BeamError("this download is not a beam file (expired or wrong code?)")
        salt, prefix = head[5:21], head[21:28]
        aes = AESGCM(_key(secret, salt))

        def chunk(index):
            raw = fin.read(5)
            if len(raw) < 5:
                raise BeamError("download is incomplete; try again")
            last, size = struct.unpack("!BI", raw)
            if size > CHUNK + 16:
                raise wrong
            data = fin.read(size)
            try:
                return bool(last), aes.decrypt(
                    _nonce(prefix, index, bool(last)), data, None
                )
            except InvalidTag:
                raise wrong from None

        _, meta_raw = chunk(0)
        meta = json.loads(meta_raw)
        with open(dst, "wb") as fout:
            index, last = 1, False
            while not last:
                last, data = chunk(index)
                fout.write(data)
                index += 1
        if fin.read(1):
            raise wrong
    return meta
