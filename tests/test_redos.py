"""Tests for the ReDoS guards (redos.py + wiring in app/engine/monitor).

Covers: static rejection at creation (layer 1), the wall-clock guard
(layer 2), and the scheduler quarantine (layer 3).
"""

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

import monitor
import redos
import store
from app import create_app

# --- Layer 1: static checker ------------------------------------------------

SAFE_PATTERNS = [
    r"^[A-Z]{3}-\d+$",
    r"\d+",
    r"[a-z]+",
    r"(a|b)+",
    r"(ab)+",
    r"(yes|no)",
    r"a+|b+",
    r"(ab|cd)+",
    r"foo|bar",
    r"[A-Z]{2,4}",
    r"\d{4}-\d{2}",
    r"[^@\s]+@[^@\s]+\.[^@\s]+",
    r"(prefix-)?\d+",
    r"(a|b|c)+",
    r"\w+@\w+",
    r"(?i)abc",
    r"(?:ab)+",
    r"a{2,5}",
    r"[\b]",
    r"\x41+",
    r"(?P<x>a)b",
]

UNSAFE_PATTERNS = [
    r"(a+)+$",
    r"(a*)*",
    r"(a+)*",
    r"(\d+)+",
    r"((a+))+",
    r"(a|a)+",
    r"(a|ab)+",
    r"(\w|\d)+",
    r"(a|)+",
    r"(a)\1",
    r"(\w+)\s+\1",
    r"(a+)?",
    r"(.*)+",
    r"(a?)+",
]


@pytest.mark.parametrize("pattern", SAFE_PATTERNS)
def test_safe_patterns_allowed(pattern):
    redos.validate_pattern(pattern)


@pytest.mark.parametrize("pattern", UNSAFE_PATTERNS)
def test_unsafe_patterns_rejected(pattern):
    with pytest.raises(redos.PatternUnsafeError):
        redos.validate_pattern(pattern)


def test_overlong_pattern_rejected():
    with pytest.raises(redos.PatternUnsafeError, match="max 500"):
        redos.validate_pattern("a" * 501)


def test_invalid_pattern_reports_not_valid():
    with pytest.raises(redos.PatternUnsafeError, match="not a valid regex"):
        redos.validate_pattern("[invalid")


# --- Layer 2: wall-clock guard ----------------------------------------------


def test_safe_fullmatch_timeout():
    # (a|aa)+$ on 30 a's + "b" is slow under the regex engine too
    # (measured: trips a 0.5 s timeout); the guard must fire promptly.
    started = time.time()
    with pytest.raises(redos.PatternTimeoutError):
        redos.safe_fullmatch(r"(a|aa)+$", "a" * 30 + "b", timeout=0.1)
    assert time.time() - started < 5


def test_safe_fullmatch_normal_matches():
    compiled = redos.compile_pattern(r"\d+")
    assert redos.safe_fullmatch(compiled, "123") is True
    assert redos.safe_fullmatch(compiled, "ab") is False


# --- Layer 1 via the app: creation rejects ----------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAWATCH_NO_SCHEDULER", "1")
    app = create_app(db_path=str(tmp_path / "test.db"))
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client()


@pytest.fixture
def authed_client(client):
    client.post(
        "/register",
        data={"username": "tester", "password": "password123"},
        follow_redirects=True,
    )
    return client


def _dataset_form(pattern):
    return {
        "dataset_name": "orders",
        "col_name": ["order_id", "code"],
        "col_type": ["integer", "string"],
        "col_required": ["yes", "yes"],
        "col_unique": ["yes", "no"],
        "col_min": ["", ""],
        "col_max": ["", ""],
        "col_pattern": ["", pattern],
        "col_allowed": ["", ""],
    }


def test_creation_rejects_catastrophic_pattern(authed_client):
    resp = authed_client.post(
        "/datasets/new",
        data=_dataset_form("(a+)+$"),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"is not allowed" in resp.data
    assert b"nested quantifier" in resp.data


def test_creation_accepts_safe_pattern(authed_client):
    resp = authed_client.post(
        "/datasets/new",
        data=_dataset_form(r"[A-Z]{3}-\d+"),
        follow_redirects=False,
    )
    assert resp.status_code == 302


# --- Layer 3: scheduler quarantine ------------------------------------------

EVIL_CONTRACT = {
    "name": "evil",
    "columns": [
        {
            "name": "code",
            "type": "string",
            "required": True,
            "unique": False,
            "min": None,
            "max": None,
            "pattern": r"(a|aa)+$",
            "allowed": None,
        }
    ],
}

EVIL_CSV = "code\n" + "a" * 30 + "b\n"
SAFE_CSV = "code\naab\n"


@pytest.fixture
def legacy_db(tmp_path):
    """A dataset created BEFORE the fix: catastrophic pattern stored
    directly, bypassing the creation-time check."""
    path = str(tmp_path / "test.db")
    store.init_db(path)
    user_id = store.create_user(path, "tester", "hash")
    dataset_id = store.create_dataset(path, user_id, "evil", EVIL_CONTRACT)
    dataset = store.get_dataset(path, dataset_id)
    store.set_last_csv(path, dataset_id, EVIL_CSV, "evil.csv")
    store.upsert_schedule(path, dataset_id, 15, True)
    return path, dataset


def _future(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_scheduler_runs_once_then_quarantines(legacy_db):
    path, dataset = legacy_db
    started = time.time()

    made = monitor.run_due_checks(path, now=_future(16))
    assert time.time() - started < 15  # 2 s guard, not a 120 s hang
    assert len(made) == 1
    assert store.is_pattern_blocked(path, dataset["id"])
    run = store.get_run(path, made[0]["run_id"])
    assert run["score"] == 0.0
    assert any(v["rule"] == "pattern" for v in run["violations"])

    # Due again, but blocked: the scheduler must not re-run it.
    made_again = monitor.run_due_checks(path, now=_future(32))
    assert made_again == []
    assert len(store.list_runs(path, dataset["id"])) == 1


def test_manual_run_after_pattern_fixed_clears_block(legacy_db):
    path, dataset = legacy_db
    monitor.run_due_checks(path, now=_future(16))
    assert store.is_pattern_blocked(path, dataset["id"])

    # A statically-unsafe pattern can never be unblocked by retry: the
    # runtime check rejects it on every run, by design. The recovery is
    # fixing the pattern. No edit route exists yet, so the test simulates
    # a future contract-edit flow with a direct contract update.
    contract = json.loads(json.dumps(dataset["contract"]))
    contract["columns"] = [dict(c, pattern=r"[a-z]+") for c in contract["columns"]]
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE datasets SET contract_json = ? WHERE id = ?",
        (json.dumps(contract), dataset["id"]),
    )
    conn.commit()
    conn.close()

    fixed = store.get_dataset(path, dataset["id"])
    _run_id, result = monitor.execute_run(path, fixed, SAFE_CSV, "safe.csv")
    assert result["passed"] is True
    assert store.is_pattern_blocked(path, dataset["id"]) is False


def test_blocked_flag_survives_reload(legacy_db):
    path, dataset = legacy_db
    store.set_pattern_blocked(path, dataset["id"], True)
    assert store.get_dataset(path, dataset["id"])["pattern_blocked"] is True
    # init_db re-runs the migration on an existing database without error.
    store.init_db(path)
    assert store.is_pattern_blocked(path, dataset["id"]) is True


def test_blocked_column_migrates_existing_db(tmp_path):
    # A database created by the pre-fix init_db gains the column.
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE datasets (id INTEGER PRIMARY KEY, user_id INTEGER,"
        " name TEXT, contract_json TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()
    store.init_db(path)
    cols = [
        row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(datasets)")
    ]
    assert "pattern_blocked" in cols
    assert store.is_pattern_blocked(path, 999) is False
