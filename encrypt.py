import os
import json
import base64
from secrets import token_bytes
from pathlib import Path

from dotenv import load_dotenv
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT_DIR = Path(__file__).resolve().parent
PLAIN_COOKIE_FILE = ROOT_DIR / "cookies.json"
ENCRYPTED_COOKIE_FILE = ROOT_DIR / "cookies.json.encrypted"


def _derive_key_from_password(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(password)


def _encrypt_bytes(data: bytes, password: str) -> dict:
    salt = token_bytes(16)
    nonce = token_bytes(12)
    key = _derive_key_from_password(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, data, None)
    return {
        "v": 1,
        "s": base64.b64encode(salt).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def main() -> None:
    load_dotenv()
    key = os.getenv("DECRYPT_KEY")
    if not key:
        raise RuntimeError("DECRYPT_KEY is missing in environment/.env")

    if not PLAIN_COOKIE_FILE.exists():
        raise FileNotFoundError(f"{PLAIN_COOKIE_FILE} not found to encrypt")

    with PLAIN_COOKIE_FILE.open("rb") as f:
        plaintext = f.read()

    payload = _encrypt_bytes(plaintext, key)
    with ENCRYPTED_COOKIE_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    try:
        PLAIN_COOKIE_FILE.unlink()
    except Exception:
        pass

    print(f"[OK] Encrypted cookies written to {ENCRYPTED_COOKIE_FILE}", flush=True)


if __name__ == "__main__":
    main()
