import pytest
from test_app import make_orders_dataset

import store
from app import create_app


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


def test_schedule_form_enables_monitoring(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    resp = authed_client.post(
        f"/datasets/{dataset_id}/schedule",
        data={"interval_minutes": "60", "enabled": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    page = authed_client.get(f"/datasets/{dataset_id}")
    assert b"Hourly" in page.data
    assert b"Next run" in page.data


def test_schedule_form_shows_monitoring_off_by_default(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    page = authed_client.get(f"/datasets/{dataset_id}")
    assert b"Monitoring is off" in page.data


def test_invalid_interval_shows_error(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    resp = authed_client.post(
        f"/datasets/{dataset_id}/schedule",
        data={"interval_minutes": "999", "enabled": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Pick a valid interval." in resp.data


def test_run_now_without_stored_file_shows_error(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    resp = authed_client.post(
        f"/datasets/{dataset_id}/run-now",
        data={},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"there is nothing to re-check yet" in resp.data


def test_run_now_after_sample_run_creates_second_run(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    first = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"sample": "clean"},
        follow_redirects=False,
    )
    assert first.status_code == 302
    first_run_id = first.location.rstrip("/").split("/")[-1]

    second = authed_client.post(
        f"/datasets/{dataset_id}/run-now",
        data={},
        follow_redirects=False,
    )
    assert second.status_code == 302
    second_run_id = second.location.rstrip("/").split("/")[-1]
    assert second_run_id != first_run_id

    page = authed_client.get(f"/runs/{second_run_id}")
    assert b"Column profiles" in page.data
    assert b"order_id" in page.data


def test_score_rule_creates_alert_visible_on_alerts_page(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    resp = authed_client.post(
        f"/datasets/{dataset_id}/alert-rules",
        data={"score_below": "99", "errors_above": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    run = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"sample": "dirty"},
        follow_redirects=False,
    )
    assert run.status_code == 302

    page = authed_client.get("/alerts")
    assert b"fell below 99.0" in page.data
    assert b"orders" in page.data

    # The nav bar shows the unread count.
    home = authed_client.get("/")
    assert b"Alerts (1)" in home.data


def test_invalid_alert_rules_show_errors(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    bad_score = authed_client.post(
        f"/datasets/{dataset_id}/alert-rules",
        data={"score_below": "abc", "errors_above": ""},
        follow_redirects=True,
    )
    assert b"Score threshold must be a number between 0 and 100." in bad_score.data
    bad_errors = authed_client.post(
        f"/datasets/{dataset_id}/alert-rules",
        data={"score_below": "", "errors_above": "-1"},
        follow_redirects=True,
    )
    assert b"Error threshold must be a whole number of 0 or more." in bad_errors.data


def test_acknowledge_clears_unread_count(authed_client, tmp_path):
    dataset_id = make_orders_dataset(authed_client)
    authed_client.post(
        f"/datasets/{dataset_id}/alert-rules",
        data={"score_below": "99", "errors_above": ""},
    )
    authed_client.post(f"/datasets/{dataset_id}/run", data={"sample": "dirty"})

    db_path = str(tmp_path / "test.db")
    alerts = store.list_alerts(db_path, 1)
    assert len(alerts) == 1
    alert_id = alerts[0]["id"]

    resp = authed_client.post(
        f"/alerts/{alert_id}/acknowledge",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    page = authed_client.get("/alerts")
    assert b"Acknowledged" in page.data
    assert store.unread_alert_count(db_path, 1) == 0

    home = authed_client.get("/")
    assert b"Alerts (1)" not in home.data


def test_other_user_sees_no_alerts_and_cannot_acknowledge(
    authed_client, client, tmp_path
):
    dataset_id = make_orders_dataset(authed_client)
    authed_client.post(
        f"/datasets/{dataset_id}/alert-rules",
        data={"score_below": "99", "errors_above": ""},
    )
    authed_client.post(f"/datasets/{dataset_id}/run", data={"sample": "dirty"})

    db_path = str(tmp_path / "test.db")
    alert_id = store.list_alerts(db_path, 1)[0]["id"]

    client.post(
        "/register",
        data={"username": "other", "password": "password123"},
        follow_redirects=True,
    )
    page = client.get("/alerts")
    assert page.status_code == 200
    assert b"fell below" not in page.data

    resp = client.post(f"/alerts/{alert_id}/acknowledge", data={})
    assert resp.status_code == 404


def test_schedule_and_alert_routes_reject_other_user(authed_client, client):
    dataset_id = make_orders_dataset(authed_client)
    client.post(
        "/register",
        data={"username": "other", "password": "password123"},
        follow_redirects=True,
    )
    assert (
        client.post(
            f"/datasets/{dataset_id}/schedule",
            data={"interval_minutes": "60", "enabled": "on"},
        ).status_code
        == 404
    )
    assert (
        client.post(f"/datasets/{dataset_id}/run-now", data={}).status_code == 404
    )
    assert (
        client.post(
            f"/datasets/{dataset_id}/alert-rules",
            data={"score_below": "99", "errors_above": ""},
        ).status_code
        == 404
    )
