import json

import pytest

import store


def test_create_and_get_user(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    assert isinstance(user_id, int)

    user = store.get_user(db, "alice")
    assert user is not None
    assert user["username"] == "alice"
    assert user["password_hash"] == "hash123"
    assert "id" in user
    assert "created_at" in user

    assert store.get_user(db, "bob") is None


def test_create_duplicate_user(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    store.create_user(db, "alice", "hash123")
    with pytest.raises(ValueError, match="username taken"):
        store.create_user(db, "alice", "hash456")


def test_create_and_get_dataset(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    contract = {
        "name": "sales",
        "columns": [
            {"name": "amount", "type": "number", "required": True,
             "unique": False, "min": 0, "max": None, "pattern": None,
             "allowed": None},
            {"name": "name", "type": "string", "required": False,
             "unique": False, "min": None, "max": None, "pattern": None,
             "allowed": None},
        ],
    }

    dataset_id = store.create_dataset(db, user_id, "sales", contract)
    assert isinstance(dataset_id, int)

    dataset = store.get_dataset(db, dataset_id)
    assert dataset is not None
    assert dataset["id"] == dataset_id
    assert dataset["user_id"] == user_id
    assert dataset["name"] == "sales"
    assert dataset["contract"] == contract

    assert store.get_dataset(db, 999) is None


def test_list_datasets_empty_and_with_datasets(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    assert store.list_datasets(db, user_id) == []

    ds1_id = store.create_dataset(db, user_id, "sales", {"columns": []})
    ds2_id = store.create_dataset(db, user_id, "orders", {"columns": []})

    datasets = store.list_datasets(db, user_id)
    assert len(datasets) == 2
    ids = {d["id"] for d in datasets}
    assert ids == {ds1_id, ds2_id}

    names = {d["name"] for d in datasets}
    assert names == {"sales", "orders"}

    for d in datasets:
        assert d["run_count"] == 0
        assert d["last_score"] is None
        assert "created_at" in d


def test_save_and_get_run(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    dataset_id = store.create_dataset(db, user_id, "sales", {"columns": []})

    result = {
        "score": 90.0,
        "passed": False,
        "total_rows": 2,
        "checks_run": 10,
        "violations": [
            {
                "row": 2,
                "column": "amount",
                "rule": "range",
                "severity": "error",
                "message": "amount -5 is below the minimum 0",
                "value": "-5",
            }
        ],
        "column_stats": {
            "amount": {"nulls": 0, "distinct": 2, "violations": 1}
        },
        "summary": {"errors": 1, "warnings": 0},
    }

    run_id = store.save_run(db, dataset_id, "sales_jan.csv", result)
    assert isinstance(run_id, int)

    run = store.get_run(db, run_id)
    assert run is not None
    assert run["id"] == run_id
    assert run["dataset_id"] == dataset_id
    assert run["filename"] == "sales_jan.csv"
    assert run["score"] == 90.0
    assert run["errors"] == 1
    assert run["warnings"] == 0
    assert run["total_rows"] == 2
    assert run["checks_run"] == 10

    violations = run["violations"]
    assert len(violations) == 1
    assert violations[0]["row"] == 2
    assert violations[0]["column"] == "amount"
    assert violations[0]["rule"] == "range"

    assert run["column_stats"] == {
        "amount": {"nulls": 0, "distinct": 2, "violations": 1}
    }

    assert store.get_run(db, 999) is None


def test_list_runs_order(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    dataset_id = store.create_dataset(db, user_id, "sales", {"columns": []})

    run1_id = store.save_run(
        db,
        dataset_id,
        "first.csv",
        {
            "score": 80.0,
            "passed": False,
            "total_rows": 5,
            "checks_run": 5,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 0, "warnings": 0},
        },
    )

    run2_id = store.save_run(
        db,
        dataset_id,
        "second.csv",
        {
            "score": 95.0,
            "passed": True,
            "total_rows": 10,
            "checks_run": 8,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 0, "warnings": 0},
        },
    )

    runs = store.list_runs(db, dataset_id)
    assert len(runs) == 2
    assert runs[0]["id"] == run2_id
    assert runs[0]["filename"] == "second.csv"
    assert runs[1]["id"] == run1_id
    assert runs[1]["filename"] == "first.csv"


def test_list_datasets_last_score(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    dataset_id = store.create_dataset(db, user_id, "sales", {"columns": []})

    store.save_run(
        db,
        dataset_id,
        "run1.csv",
        {
            "score": 88.5,
            "passed": False,
            "total_rows": 3,
            "checks_run": 4,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 1, "warnings": 0},
        },
    )

    datasets = store.list_datasets(db, user_id)
    assert len(datasets) == 1
    assert datasets[0]["run_count"] == 1
    assert datasets[0]["last_score"] == 88.5


def test_latest_runs(tmp_path):
    db = str(tmp_path / "test.db")
    store.init_db(db)

    user_id = store.create_user(db, "alice", "hash123")
    ds1_id = store.create_dataset(db, user_id, "sales", {"columns": []})
    ds2_id = store.create_dataset(db, user_id, "orders", {"columns": []})

    run1_id = store.save_run(
        db,
        ds1_id,
        "sales_jan.csv",
        {
            "score": 70.0,
            "passed": False,
            "total_rows": 1,
            "checks_run": 2,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 2, "warnings": 0},
        },
    )

    run2_id = store.save_run(
        db,
        ds2_id,
        "orders_jan.csv",
        {
            "score": 85.0,
            "passed": False,
            "total_rows": 4,
            "checks_run": 6,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 0, "warnings": 1},
        },
    )

    run3_id = store.save_run(
        db,
        ds1_id,
        "sales_feb.csv",
        {
            "score": 92.0,
            "passed": True,
            "total_rows": 8,
            "checks_run": 7,
            "violations": [],
            "column_stats": {},
            "summary": {"errors": 0, "warnings": 0},
        },
    )

    latest = store.latest_runs(db, user_id)
    assert len(latest) == 3

    assert latest[0]["id"] == run3_id
    assert latest[0]["dataset_id"] == ds1_id
    assert latest[0]["dataset_name"] == "sales"
    assert latest[0]["filename"] == "sales_feb.csv"
    assert latest[0]["score"] == 92.0

    assert latest[1]["id"] == run2_id
    assert latest[1]["dataset_id"] == ds2_id
    assert latest[1]["dataset_name"] == "orders"
    assert latest[1]["filename"] == "orders_jan.csv"

    assert latest[2]["id"] == run1_id
    assert latest[2]["dataset_id"] == ds1_id
    assert latest[2]["dataset_name"] == "sales"
    assert latest[2]["filename"] == "sales_jan.csv"
