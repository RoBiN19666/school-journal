"""Шифрование секретов.

* Пароль SMTP шифруется ключом из ``SECRET_KEY`` (функции ``encrypt``/``decrypt``):
  ключ не хранится в базе, поэтому дамп базы без ``SECRET_KEY`` пароль не раскрывает.
  Используется потоковый шифр на SHA-256.
* Резервная копия может шифроваться парольной фразой администратора
  (``encrypt_bytes``/``decrypt_bytes``): PBKDF2-HMAC-SHA256, потоковый шифр
  HMAC-SHA256 и метка целостности HMAC, поэтому подмена файла обнаруживается.
"""
import base64
import hashlib
import hmac
import os

from django.conf import settings

PREFIX = "enc:"
BLOCK = 32

# Параметры шифрования резервной копии.
FILE_MAGIC = b"SJENC1\n"
FILE_SALT = 16
FILE_NONCE = 16
FILE_TAG = 32
PBKDF2_ITERATIONS = 200_000


def _key():
    secret = str(settings.SECRET_KEY).encode("utf-8")
    salt = b"school-journal-secret-v1"
    return hashlib.sha256(salt + secret).digest()


def _keystream(length, nonce):
    key = _key()
    stream = b""
    counter = 0
    while len(stream) < length:
        stream += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return stream[:length]


def encrypt(value):
    """Шифрует строку. Пустое значение возвращаем как есть."""
    if not value:
        return ""
    data = str(value).encode("utf-8")
    nonce = os.urandom(16)
    stream = _keystream(len(data), nonce)
    encrypted = bytes(a ^ b for a, b in zip(data, stream))
    payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    return PREFIX + payload


def decrypt(value):
    """Расшифровывает строку. Если это не наш формат — возвращает как есть."""
    if not value or not str(value).startswith(PREFIX):
        return value or ""
    payload = str(value)[len(PREFIX) :]
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    except (ValueError, TypeError):
        return ""
    nonce, encrypted = raw[:16], raw[16:]
    if len(nonce) != 16:
        return ""
    stream = _keystream(len(encrypted), nonce)
    data = bytes(a ^ b for a, b in zip(encrypted, stream))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


# --- Шифрование файлов (резервная копия) --------------------------------------
def _material(passphrase, salt):
    """Ключ шифрования и ключ целостности из парольной фразы."""
    derived = hashlib.pbkdf2_hmac(
        "sha256", str(passphrase).encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=64
    )
    return derived[:32], derived[32:]


def _file_stream(key, nonce, length):
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        stream += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(stream[:length])


def is_encrypted_blob(data):
    """Похоже ли содержимое на зашифрованную копию."""
    return bytes(data[: len(FILE_MAGIC)]) == FILE_MAGIC


def encrypt_bytes(data, passphrase):
    """Шифрует данные парольной фразой с проверкой целостности."""
    if not passphrase:
        raise ValueError("Нужна парольная фраза для шифрования.")
    salt = os.urandom(FILE_SALT)
    nonce = os.urandom(FILE_NONCE)
    enc_key, mac_key = _material(passphrase, salt)
    stream = _file_stream(enc_key, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    return FILE_MAGIC + salt + nonce + tag + cipher


def decrypt_bytes(blob, passphrase):
    """Расшифровывает данные. Неверная фраза или испорченный файл — ValueError."""
    raw = bytes(blob)
    if not is_encrypted_blob(raw):
        raise ValueError("Файл не зашифрован.")
    head = len(FILE_MAGIC)
    salt = raw[head : head + FILE_SALT]
    nonce = raw[head + FILE_SALT : head + FILE_SALT + FILE_NONCE]
    tag = raw[head + FILE_SALT + FILE_NONCE : head + FILE_SALT + FILE_NONCE + FILE_TAG]
    cipher = raw[head + FILE_SALT + FILE_NONCE + FILE_TAG :]
    if len(salt) != FILE_SALT or len(nonce) != FILE_NONCE or len(tag) != FILE_TAG:
        raise ValueError("Файл повреждён: нет заголовка шифрования.")
    if not passphrase:
        raise ValueError("Нужна парольная фраза для расшифровки.")
    enc_key, mac_key = _material(passphrase, salt)
    expected = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("Неверная парольная фраза или файл повреждён.")
    stream = _file_stream(enc_key, nonce, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream))
