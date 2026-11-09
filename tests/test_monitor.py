import pytest

import monitor
import store

CONTRACT = {
    "name": "orders",
    "columns": [
        {"name": "order_id", "type": "integer", "required": True, "unique": True},
        {"name": "amount", "type": "number", "required": True, "min": 0},
        {"name": "status", "type": "string", "required": True,
         "allowed": ["paid", "shipped"]},
    ],
}

CLEAN_CSV = "order_id,amount,status\n1,10,paid\n2,20,shipped\n"
DIRTY_CSV = "order_id,amount,status\n1,-5,paid\n1,20,lost\n"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    store.init_db(path)
    user_id = store.create_user(path, "tester", "hash")
    dataset_id = store.create_dataset(path, user_id, "orders", CONTRACT)
    dataset = store.get_dataset(path, dataset_id)
    return path, user_id, dataset


def test_execute_run_saves_run_profile_and_last_csv(db):
    path, _user_id, dataset = db
    run_id, result = monitor.execute_run(path, dataset, CLEAN_CSV, "clean.csv")

    assert result["score"] == 100.0
    saved = store.get_run(path, run_id)
    assert saved["score"] == result["score"]
    assert saved["source"] == "manual"
    assert saved["profile"]["order_id"]["min"] == 1
    assert saved["profile"]["order_id"]["max"] == 2
    assert saved["profile"]["status"]["top_values"] == [
        {"value": "paid", "count": 1},
        {"value": "shipped", "count": 1},
    ]
    assert store.get_last_csv(path, dataset["id"]) == {
        "text": CLEAN_CSV,
        "filename": "clean.csv",
    }


def test_score_alert_fires_below_threshold_only(db):
    path, _user_id, dataset = db
    store.set_alert_rules(path, dataset["id"], 99.0, None)
    run_id, _result = monitor.execute_run(path, dataset, DIRTY_CSV, "dirty.csv")
    alerts = store.list_alerts(path, 1)
    assert len(alerts) == 1
    assert alerts[0]["rule"] == "score_below"
    assert alerts[0]["run_id"] == run_id
    assert "fell below 99.0" in alerts[0]["message"]

    # Same dirty file, threshold below the score: no alert.
    store.set_alert_rules(path, dataset["id"], 50.0, None)
    monitor.execute_run(path, dataset, DIRTY_CSV, "dirty.csv")
    assert len(store.list_alerts(path, 1)) == 1


def test_errors_alert_fires_above_threshold_only(db):
    path, _user_id, dataset = db
    store.set_alert_rules(path, dataset["id"], None, 2)
    monitor.execute_run(path, dataset, DIRTY_CSV, "dirty.csv")
    alerts = store.list_alerts(path, 1)
    assert len(alerts) == 1
    assert alerts[0]["rule"] == "errors_above"
    assert "3 errors" in alerts[0]["message"]

    # Equal to the threshold does not fire (strictly above).
    store.set_alert_rules(path, dataset["id"], None, 3)
    monitor.execute_run(path, dataset, DIRTY_CSV, "dirty.csv")
    assert len(store.list_alerts(path, 1)) == 1


def test_both_rules_fire_together(db):
    path, _user_id, dataset = db
    store.set_alert_rules(path, dataset["id"], 99.0, 2)
    run_id, _result = monitor.execute_run(path, dataset, DIRTY_CSV, "dirty.csv")
    alerts = store.list_alerts(path, 1)
    assert len(alerts) == 2
    assert {a["rule"] for a in alerts} == {"score_below", "errors_above"}
    assert all(a["run_id"] == run_id for a in alerts)


def test_run_due_checks_runs_due_schedule_and_advances(db):
    path, _user_id, dataset = db
    monitor.execute_run(path, dataset, CLEAN_CSV, "clean.csv")
    store.upsert_schedule(path, dataset["id"], 60, True)
    schedule = store.get_schedule(path, dataset["id"])
    store.touch_schedule(path, schedule["id"], None, "2026-11-09T09:00:00+00:00")

    made = monitor.run_due_checks(path, now="2026-11-09T10:00:00+00:00")
    assert len(made) == 1
    assert made[0]["dataset_id"] == dataset["id"]

    schedule = store.get_schedule(path, dataset["id"])
    assert schedule["last_run_at"] == "2026-11-09T10:00:00+00:00"
    assert schedule["next_run_at"] == "2026-11-09T11:00:00+00:00"

    # The scheduled run is stored with source "scheduled".
    assert store.get_run(path, made[0]["run_id"])["source"] == "scheduled"


def test_run_due_checks_skips_disabled_schedule(db):
    path, _user_id, dataset = db
    monitor.execute_run(path, dataset, CLEAN_CSV, "clean.csv")
    store.upsert_schedule(path, dataset["id"], 60, False)
    assert monitor.run_due_checks(path, now="2026-11-09T10:00:00+00:00") == []


def test_run_due_checks_advances_schedule_without_stored_file(db):
    path, _user_id, dataset = db
    store.upsert_schedule(path, dataset["id"], 60, True)
    schedule = store.get_schedule(path, dataset["id"])
    store.touch_schedule(path, schedule["id"], None, "2026-11-09T09:00:00+00:00")

    made = monitor.run_due_checks(path, now="2026-11-09T10:00:00+00:00")
    assert made == []
    assert store.list_runs(path, dataset["id"]) == []

    schedule = store.get_schedule(path, dataset["id"])
    assert schedule["next_run_at"] == "2026-11-09T10:00:00+00:00"


def test_run_due_checks_skips_bad_stored_csv(db):
    path, _user_id, dataset = db
    monitor.execute_run(path, dataset, CLEAN_CSV, "clean.csv")
    store.set_last_csv(path, dataset["id"], "a,a\n1,2\n", "bad.csv")
    store.upsert_schedule(path, dataset["id"], 60, True)
    schedule = store.get_schedule(path, dataset["id"])
    store.touch_schedule(path, schedule["id"], None, "2026-11-09T09:00:00+00:00")

    made = monitor.run_due_checks(path, now="2026-11-09T10:00:00+00:00")
    assert made == []
    # One run exists (the manual clean run); the bad file made no new run.
    assert len(store.list_runs(path, dataset["id"])) == 1
    schedule = store.get_schedule(path, dataset["id"])
    assert schedule["next_run_at"] == "2026-11-09T10:00:00+00:00"
