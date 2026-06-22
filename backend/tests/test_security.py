from app.core.security import encrypt, decrypt


def test_encrypt_roundtrip():
    plaintext = b"the quick brown fox"
    token = encrypt(plaintext)
    assert isinstance(token, str)
    assert token != plaintext.decode()
    assert decrypt(token) == plaintext


def test_encrypt_str_input():
    token = encrypt("hello world")
    assert decrypt(token).decode() == "hello world"


def test_encrypt_unique_per_call():
    a = encrypt("same input")
    b = encrypt("same input")
    assert a != b  # nonce is random
