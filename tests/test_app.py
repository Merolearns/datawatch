import pytest
from app import create_app


DATASET_FORM = {
    "dataset_name": "orders",
    "col_name": ["order_id", "amount"],
    "col_type": ["integer", "number"],
    "col_required": ["yes", "yes"],
    "col_unique": ["yes", "no"],
    "col_min": ["", "0"],
    "col_max": ["", ""],
    "col_pattern": ["", ""],
    "col_allowed": ["", ""],
}


def make_orders_dataset(client):
    resp = client.post("/datasets/new", data=DATASET_FORM, follow_redirects=False)
    assert resp.status_code == 302
    return resp.location.rstrip("/").split("/")[-1]


@pytest.fixture
def client(tmp_path):
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


def test_dashboard_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_register_and_dashboard(client):
    resp = client.post(
        "/register",
        data={"username": "newbie", "password": "password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Your datasets" in resp.data


def test_register_same_username_shows_taken(client):
    client.post("/register", data={"username": "dup", "password": "password123"})
    resp = client.post(
        "/register",
        data={"username": "dup", "password": "different123"},
        follow_redirects=True,
    )
    assert b"taken" in resp.data.lower()


def test_register_short_password_no_login(client):
    resp = client.post(
        "/register",
        data={"username": "shortpass", "password": "short"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    home = client.get("/")
    assert home.status_code == 302
    assert "/login" in home.location


def test_logout_and_login(authed_client):
    authed_client.get("/logout")
    resp = authed_client.post(
        "/login",
        data={"username": "tester", "password": "password123"},
        follow_redirects=True,
    )
    assert b"Your datasets" in resp.data

    authed_client.get("/logout")
    bad = authed_client.post(
        "/login",
        data={"username": "tester", "password": "wrongpass"},
        follow_redirects=True,
    )
    assert b"Wrong username or password." in bad.data


def test_create_dataset(authed_client):
    resp = authed_client.post("/datasets/new", data=DATASET_FORM, follow_redirects=False)
    assert resp.status_code == 302
    dataset_id = resp.location.rstrip("/").split("/")[-1]
    page = authed_client.get(f"/datasets/{dataset_id}")
    assert page.status_code == 200
    assert b"order_id" in page.data
    assert b"amount" in page.data


def test_run_samples(authed_client):
    dataset_id = make_orders_dataset(authed_client)

    dirty = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"sample": "dirty"},
        follow_redirects=False,
    )
    assert dirty.status_code == 302
    dirty_run_id = dirty.location.rstrip("/").split("/")[-1]
    dirty_page = authed_client.get(f"/runs/{dirty_run_id}")
    assert b"Fail" in dirty_page.data
    assert b"amount" in dirty_page.data or b"email" in dirty_page.data

    clean = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"sample": "clean"},
        follow_redirects=False,
    )
    assert clean.status_code == 302
    clean_run_id = clean.location.rstrip("/").split("/")[-1]
    clean_page = authed_client.get(f"/runs/{clean_run_id}")
    assert b"Pass" in clean_page.data
    assert b"100" in clean_page.data


def test_empty_csv_text_shows_error(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    resp = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"csv_text": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"error" in resp.data.lower()


def test_api_dataset_runs(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    authed_client.post(f"/datasets/{dataset_id}/run", data={"sample": "clean"})
    api = authed_client.get(f"/api/datasets/{dataset_id}/runs")
    assert api.status_code == 200
    data = api.get_json()
    runs = data if isinstance(data, list) else data.get("runs", [])
    assert len(runs) >= 1


def test_export_run_csv(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    run = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"sample": "dirty"},
        follow_redirects=False,
    )
    run_id = run.location.rstrip("/").split("/")[-1]
    resp = authed_client.get(f"/runs/{run_id}/export")
    text = resp.data.decode()
    assert "row,rule,severity,message,value" in text
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) > 1


def test_other_user_cannot_view_dataset(authed_client, tmp_path):
    dataset_id = make_orders_dataset(authed_client)

    other_app = create_app(db_path=str(tmp_path / "test.db"))
    other_app.config["TESTING"] = True
    other_app.config["WTF_CSRF_ENABLED"] = False
    other = other_app.test_client()
    other.post(
        "/register",
        data={"username": "other", "password": "password123"},
        follow_redirects=True,
    )
    resp = other.get(f"/datasets/{dataset_id}")
    assert resp.status_code == 404


def _bad_dataset_form(**overrides):
    form = {
        "dataset_name": "orders",
        "col_name": ["order_id", "amount"],
        "col_type": ["integer", "number"],
        "col_required": ["yes", "yes"],
        "col_unique": ["yes", "no"],
        "col_min": ["", "0"],
        "col_max": ["", ""],
        "col_pattern": ["", ""],
        "col_allowed": ["", ""],
    }
    form.update(overrides)
    return form


def test_duplicate_column_names_rejected(authed_client):
    resp = authed_client.post(
        "/datasets/new",
        data=_bad_dataset_form(col_name=["amount", "amount"]),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"listed twice" in resp.data


def test_bad_regex_rejected(authed_client):
    resp = authed_client.post(
        "/datasets/new",
        data=_bad_dataset_form(col_pattern=["[invalid", ""]),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not a valid regex" in resp.data


def test_non_numeric_min_rejected(authed_client):
    resp = authed_client.post(
        "/datasets/new",
        data=_bad_dataset_form(col_min=["", "abc"]),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"must be a number" in resp.data


def test_bom_upload_runs_clean(authed_client):
    dataset_id = make_orders_dataset(authed_client)
    csv_text = chr(0xFEFF) + "order_id,amount\n1,10\n2,20\n"
    resp = authed_client.post(
        f"/datasets/{dataset_id}/run",
        data={"csv_text": csv_text},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    run_page = authed_client.get(resp.location)
    assert b"100.0" in run_page.data


def test_api_requires_login_returns_401(client):
    resp = client.get("/api/datasets/1/runs")
    assert resp.status_code == 401
    assert resp.is_json
