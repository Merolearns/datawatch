from auth import hash_password, verify_password


def test_hash_and_verify_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong password", stored) is False


def test_hashes_differ_per_call():
    assert hash_password("same password") != hash_password("same password")


def test_malformed_stored_hash_returns_false():
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "") is False
    assert verify_password("anything", "pbkdf2_sha256$xyz") is False
