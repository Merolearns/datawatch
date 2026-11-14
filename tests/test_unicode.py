"""Gate #20 — Unicode normalization (NFKC + casefold) for usernames.

Spotify-class check: confusable variants of one identity ('ᴮᴵᴳᴮᴵᴿᴰ',
fullwidth, mixed case) must collapse to a single stored identity and must
not let a look-alike claim hijack or duplicate an existing account.
"""

import sqlite3

import pytest

import store
from app import create_app


def test_normalize_identity_folds_spotify_variant():
    assert store.normalize_identity("ᴮᴵᴳᴮᴵᴿᴰ") == "bigbird"
    assert store.normalize_identity("ＢＩＧＢＩＲＤ") == "bigbird"
    assert store.normalize_identity("BigBird") == "bigbird"
    assert store.normalize_identity("Ｍｅｒｏ") == "mero"
    assert store.normalize_identity("ﬁ") == "fi"
    assert store.normalize_identity("  Mero  ") == "mero"
    # U+0130 (İ) casefolds to 'i' + combining dot above (U+0307).
    assert store.normalize_identity("\u0130") == "i\u0307"
    assert store.normalize_identity("mero") == "mero"
    assert store.normalize_identity("") == ""


def test_confusable_username_rejected_as_taken(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)
    store.create_user(db, "Mero", "hash123")
    with pytest.raises(ValueError, match="username taken"):
        store.create_user(db, "ᴹᴱᴿᴼ", "hash456")
    with pytest.raises(ValueError, match="username taken"):
        store.create_user(db, "mero", "hash456")
    with pytest.raises(ValueError, match="username taken"):
        store.create_user(db, "ＭＥＲＯ", "hash456")


def test_get_user_finds_confusable_variant(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)
    user_id = store.create_user(db, "Mero", "hash123")
    assert store.get_user(db, "ᴹᴱᴿᴼ")["id"] == user_id
    assert store.get_user(db, "mero")["id"] == user_id
    assert store.get_user(db, "Ｍｅｒｏ")["id"] == user_id
    assert store.get_user(db, "Mero")["username"] == "mero"


def test_backfill_normalizes_legacy_rows_and_renames_collisions(tmp_path):
    # Raw rows inserted directly, bypassing create_user (pre-fix state).
    db = str(tmp_path / "legacy.db")
    store.init_db(db)
    with sqlite3.connect(db) as conn:
        for username, created in (
            ("Mero", "2026-01-01T00:00:00"),
            ("ᴹᴱᴿᴼ", "2026-01-02T00:00:00"),
            ("Ａlice", "2026-01-03T00:00:00"),
        ):
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at)"
                " VALUES (?, ?, ?)",
                (username, "hash123", created),
            )
    store.init_db(db)
    assert store.get_user(db, "mero")["username"] == "mero"
    assert store.get_user(db, "Mero")["id"] == 1  # earliest keeps the name
    renamed = store.get_user(db, "mero~2")
    assert renamed is not None and renamed["id"] == 2
    assert store.get_user(db, "Ａlice")["username"] == "alice"
    # Second init_db is a no-op (idempotent).
    store.init_db(db)
    assert store.get_user(db, "mero~2")["id"] == 2
    assert store.get_user(db, "mero")["id"] == 1


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"))
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client()


def test_register_then_login_with_confusable_variant(client):
    assert (
        client.post(
            "/register", data={"username": "Mero", "password": "password123"}
        ).status_code
        == 302
    )
    # A confusable variant cannot register a second account.
    resp = client.post(
        "/register", data={"username": "ᴹᴱᴿᴼ", "password": "password123"}
    )
    assert resp.status_code == 200
    assert b"taken" in resp.data
    # And it logs into the original account.
    assert (
        client.post(
            "/login", data={"username": "ᴹᴱᴿᴼ", "password": "password123"}
        ).status_code
        == 302
    )
