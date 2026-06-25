import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """
    Derives a 256-bit AES key from a passphrase and a salt using PBKDF2HMAC.
    Uses SHA256 and 100,000 iterations.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(passphrase.encode())


def encrypt_keypair(private_key_bytes: bytes, passphrase: str) -> bytes:
    """
    Encrypts Solana wallet private key bytes using AES-256-GCM.
    Returns: salt (16 bytes) + nonce (12 bytes) + ciphertext.
    """
    salt = os.urandom(16)
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, private_key_bytes, None)
    return salt + nonce + ciphertext


def decrypt_keypair(encrypted_bytes: bytes, passphrase: str) -> bytes:
    """
    Decrypts Solana wallet private key bytes using AES-256-GCM.
    Expects prefix of salt (16 bytes) and nonce (12 bytes).
    """
    if len(encrypted_bytes) < 28:
        raise ValueError("Encrypted key file is corrupted or too short")
    
    salt = encrypted_bytes[:16]
    nonce = encrypted_bytes[16:28]
    ciphertext = encrypted_bytes[28:]
    
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    
    # Decrypt and return original private key bytes
    return aesgcm.decrypt(nonce, ciphertext, None)
