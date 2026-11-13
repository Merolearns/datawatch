import app as app_module


def test_healthz_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAWATCH_NO_SCHEDULER", "1")
    db = tmp_path / "health.db"
    application = app_module.create_app(db_path=str(db))
    client = application.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_healthz_needs_no_login(monkeypatch, tmp_path):
    # No session: /healthz must answer without redirecting to /login.
    monkeypatch.setenv("DATAWATCH_NO_SCHEDULER", "1")
    db = tmp_path / "health2.db"
    application = app_module.create_app(db_path=str(db))
    client = application.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_datawatch_db_env(monkeypatch, tmp_path):
    # DATAWATCH_DB overrides the default database path (deploy uses it
    # to keep the DB on the persistent volume).
    monkeypatch.setenv("DATAWATCH_NO_SCHEDULER", "1")
    env_db = str(tmp_path / "env.db")
    monkeypatch.setenv("DATAWATCH_DB", env_db)
    application = app_module.create_app(db_path=None)
    assert application.config["DB_PATH"] == env_db
