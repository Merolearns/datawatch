import csv
import functools
import io
import os
import re
import secrets
import sqlite3

from flask import (
    Flask,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect

import auth
import charts
import engine
import monitor
import store


def _col(name, col_type, required=False, unique=False, min=None, max=None,
         pattern=None, allowed=None):
    return {
        "name": name,
        "type": col_type,
        "required": required,
        "unique": unique,
        "min": min,
        "max": max,
        "pattern": pattern,
        "allowed": allowed,
    }


ORDERS_CONTRACT = {
    "name": "orders",
    "columns": [
        _col("order_id", "integer", required=True, unique=True),
        _col("customer_email", "email", required=True),
        _col("amount", "number", required=True, min=0),
        _col("status", "string", required=True,
             allowed=["pending", "paid", "shipped", "refunded"]),
        _col("created_at", "date", required=True),
    ],
}


def _to_limit(raw, col_type):
    """Parse a min/max form value: float for numeric columns, ISO string for
    dates, None when blank. Returns (value, error-or-None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if col_type in ("integer", "number"):
        try:
            return float(raw), None
        except ValueError:
            return None, f"Min/max must be a number, got {raw!r}."
    return raw, None


def _parse_contract_form(form):
    """Build a contract dict from the builder form.

    Returns (contract, error): error is a plain string when the form is
    invalid (duplicate column, bad number, bad regex), else None.
    """
    names = form.getlist("col_name")
    types = form.getlist("col_type")
    requireds = form.getlist("col_required")
    uniques = form.getlist("col_unique")
    mins = form.getlist("col_min")
    maxs = form.getlist("col_max")
    patterns = form.getlist("col_pattern")
    alloweds = form.getlist("col_allowed")

    columns = []
    seen_names = set()
    for i in range(len(names)):
        name = names[i].strip()
        if not name:
            continue
        if name in seen_names:
            return None, f"Column {name!r} is listed twice."
        seen_names.add(name)

        col_type = types[i].strip() if i < len(types) else "string"
        min_raw = mins[i] if i < len(mins) else ""
        max_raw = maxs[i] if i < len(maxs) else ""
        minv, err = _to_limit(min_raw, col_type)
        if err:
            return None, f"{name}: {err}"
        maxv, err = _to_limit(max_raw, col_type)
        if err:
            return None, f"{name}: {err}"
        pattern = (
            patterns[i].strip() if i < len(patterns) and patterns[i].strip() else None
        )
        if pattern:
            try:
                re.compile(pattern)
            except re.error:
                return None, f"{name}: pattern {pattern!r} is not a valid regex."

        col = {
            "name": name,
            "type": col_type,
            "required": (requireds[i] == "yes") if i < len(requireds) else False,
            "unique": (uniques[i] == "yes") if i < len(uniques) else False,
            "min": minv,
            "max": maxv,
            "pattern": pattern,
            "allowed": None,
        }

        if i < len(alloweds):
            allowed = [x.strip() for x in alloweds[i].split(",") if x.strip()]
            if allowed:
                col["allowed"] = allowed

        columns.append(col)

    return {
        "name": form.get("dataset_name", "").strip() or "Untitled dataset",
        "columns": columns,
    }, None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "login required"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _csv_safe(value):
    """Neutralize spreadsheet formula injection in exported values."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def create_app(db_path=None):
    if db_path is None:
        # DATAWATCH_DB lets the deployed container keep its database on
        # the persistent volume (/data) instead of the app directory.
        db_path = os.environ.get("DATAWATCH_DB") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "datawatch.db"
        )

    app = Flask(__name__)
    secret = os.environ.get("DATAWATCH_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        app.logger.warning(
            "DATAWATCH_SECRET is not set; using an ephemeral secret "
            "(sessions will not survive a restart)."
        )
    app.config["SECRET_KEY"] = secret
    app.config["DB_PATH"] = db_path
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    CSRFProtect(app)

    # Create tables on first request, not at import time, so importing the
    # app (e.g. during test collection) leaves no database file behind.
    @app.before_request
    def _ensure_db():
        if not app.config.get("_DB_READY"):
            store.init_db(db_path)
            app.config["_DB_READY"] = True

    # Unread alert count for the nav bar, on every page when logged in.
    @app.before_request
    def _load_unread_alerts():
        g.unread_alerts = 0
        if session.get("user_id"):
            try:
                g.unread_alerts = store.unread_alert_count(db_path, session["user_id"])
            except Exception:
                g.unread_alerts = 0

    @app.errorhandler(413)
    def _too_large(error):
        return "That file is too large (max 5 MB).", 413

    @app.route("/healthz")
    def healthz():
        # App up + SQLite readable. No login: the deploy health check and
        # the container HEALTHCHECK hit this. A DB failure is a 503.
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            try:
                conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='users'"
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - health check must not raise
            return jsonify({"status": "error", "error": str(e)}), 503
        return jsonify({"status": "ok"})

    @app.route("/")
    @login_required
    def dashboard():
        datasets = store.list_datasets(db_path, session["user_id"])
        recent_runs = store.latest_runs(db_path, session["user_id"])
        alerts = store.list_alerts(db_path, session["user_id"], limit=50)
        recent_alerts = [a for a in alerts if not a["acknowledged"]][:5]
        return render_template(
            "dashboard.html",
            datasets=datasets,
            recent_runs=recent_runs,
            unread_alerts=store.unread_alert_count(db_path, session["user_id"]),
            recent_alerts=recent_alerts,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if len(username) < 3 or len(password) < 8:
                error = "Username must be at least 3 characters and password at least 8 characters."
            else:
                try:
                    user_id = store.create_user(
                        db_path, username, auth.hash_password(password)
                    )
                    session["user_id"] = user_id
                    session["username"] = username
                    return redirect(url_for("dashboard"))
                except ValueError as e:
                    error = str(e)
        return render_template("auth.html", mode="register", error=error)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = store.get_user(db_path, username)
            if user is None or not auth.verify_password(password, user["password_hash"]):
                error = "Wrong username or password."
            else:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="login", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/datasets/new", methods=["GET", "POST"])
    @login_required
    def dataset_new():
        if request.method == "GET":
            return render_template(
                "dataset_new.html",
                error=None,
                prefill=ORDERS_CONTRACT["columns"],
                dataset_name="orders",
            )

        contract, error = _parse_contract_form(request.form)
        if error:
            return render_template(
                "dataset_new.html",
                error=error,
                prefill=ORDERS_CONTRACT["columns"],
                dataset_name=request.form.get("dataset_name", "").strip() or "orders",
            )

        if not contract["columns"]:
            return render_template(
                "dataset_new.html",
                error="A dataset needs at least one column.",
                prefill=ORDERS_CONTRACT["columns"],
                dataset_name=request.form.get("dataset_name", "").strip() or "orders",
            )

        dataset_id = store.create_dataset(db_path, session["user_id"], contract["name"], contract)
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    def _render_dataset(dataset, error=None):
        runs = store.list_runs(db_path, dataset["id"])
        trend_svg = charts.score_trend_svg(list(reversed(runs))) if runs else ""
        return render_template(
            "dataset.html",
            dataset=dataset,
            runs=runs,
            trend_svg=trend_svg,
            schedule=store.get_schedule(db_path, dataset["id"]),
            alert_rules=store.get_alert_rules(db_path, dataset["id"]),
            error=error,
        )

    @app.route("/datasets/<int:dataset_id>")
    @login_required
    def dataset_detail(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        return _render_dataset(dataset)

    @app.route("/datasets/<int:dataset_id>/run", methods=["POST"])
    @login_required
    def dataset_run(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)

        text = None
        filename = None

        sample = request.form.get("sample")
        if sample in ("clean", "dirty"):
            sample_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "data",
                f"sample_orders_{sample}.csv",
            )
            with open(sample_path, encoding="utf-8") as f:
                text = f.read()
            filename = os.path.basename(sample_path)
        elif request.files.get("csv_file") and request.files["csv_file"].filename:
            uploaded = request.files["csv_file"]
            text = uploaded.read().decode("utf-8-sig", errors="replace")
            filename = uploaded.filename
        elif request.form.get("csv_text", "").strip():
            text = request.form.get("csv_text").strip()
            filename = "pasted.csv"

        if text is None:
            return _render_dataset(
                dataset, "Upload a CSV file, paste CSV text, or use a sample."
            )

        try:
            rows = engine.parse_csv_text(text)
        except ValueError as e:
            return _render_dataset(dataset, str(e))
        if not rows:
            return _render_dataset(dataset, "That CSV has no data rows.")

        run_id, _result = monitor.execute_run(db_path, dataset, text, filename)
        return redirect(url_for("run_detail", run_id=run_id))

    VALID_INTERVALS = (15, 60, 360, 1440)

    @app.route("/datasets/<int:dataset_id>/schedule", methods=["POST"])
    @login_required
    def dataset_schedule(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        try:
            interval = int(request.form.get("interval_minutes", "").strip())
        except ValueError:
            interval = None
        if interval not in VALID_INTERVALS:
            return _render_dataset(dataset, "Pick a valid interval.")
        enabled = request.form.get("enabled") == "on"
        store.upsert_schedule(db_path, dataset_id, interval, enabled)
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    @app.route("/datasets/<int:dataset_id>/run-now", methods=["POST"])
    @login_required
    def dataset_run_now(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        stored = store.get_last_csv(db_path, dataset_id)
        if stored is None:
            return _render_dataset(
                dataset,
                "Upload or run a file first — there is nothing to re-check yet.",
            )
        try:
            run_id, _result = monitor.execute_run(
                db_path, dataset, stored["text"], stored["filename"],
                source="scheduled",
            )
        except ValueError as e:
            return _render_dataset(dataset, str(e))
        return redirect(url_for("run_detail", run_id=run_id))

    @app.route("/datasets/<int:dataset_id>/alert-rules", methods=["POST"])
    @login_required
    def dataset_alert_rules(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        score_raw = (request.form.get("score_below") or "").strip()
        errors_raw = (request.form.get("errors_above") or "").strip()

        score_below = None
        if score_raw:
            try:
                score_below = float(score_raw)
            except ValueError:
                score_below = None
                return _render_dataset(
                    dataset, "Score threshold must be a number between 0 and 100."
                )
            if not 0 <= score_below <= 100:
                return _render_dataset(
                    dataset, "Score threshold must be a number between 0 and 100."
                )

        errors_above = None
        if errors_raw:
            try:
                errors_above = int(errors_raw)
            except ValueError:
                return _render_dataset(
                    dataset,
                    "Error threshold must be a whole number of 0 or more.",
                )
            if errors_above < 0:
                return _render_dataset(
                    dataset,
                    "Error threshold must be a whole number of 0 or more.",
                )

        store.set_alert_rules(db_path, dataset_id, score_below, errors_above)
        return redirect(url_for("dataset_detail", dataset_id=dataset_id))

    @app.route("/alerts")
    @login_required
    def alerts():
        user_alerts = store.list_alerts(db_path, session["user_id"])
        return render_template("alerts.html", alerts=user_alerts)

    @app.route("/alerts/<int:alert_id>/acknowledge", methods=["POST"])
    @login_required
    def alert_acknowledge(alert_id):
        if not store.acknowledge_alert(db_path, alert_id, session["user_id"]):
            abort(404)
        return redirect(url_for("alerts"))

    @app.route("/runs/<int:run_id>")
    @login_required
    def run_detail(run_id):
        run = store.get_run(db_path, run_id)
        if run is None:
            abort(404)
        dataset = store.get_dataset(db_path, run["dataset_id"])
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        return render_template(
            "run.html",
            run=run,
            dataset_name=dataset["name"],
            show_truncated=(len(run["violations"]) > 200),
        )

    @app.route("/runs/<int:run_id>/export")
    @login_required
    def run_export(run_id):
        run = store.get_run(db_path, run_id)
        if run is None:
            abort(404)
        dataset = store.get_dataset(db_path, run["dataset_id"])
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["row", "rule", "severity", "message", "value"])
        for v in run["violations"]:
            writer.writerow(
                [
                    v.get("row", ""),
                    v.get("rule", ""),
                    v.get("severity", ""),
                    v.get("message", ""),
                    _csv_safe(v.get("value", "")),
                ]
            )

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="run-{run_id}-violations.csv"'
            },
        )

    @app.route("/api/datasets/<int:dataset_id>/runs")
    @login_required
    def api_dataset_runs(dataset_id):
        dataset = store.get_dataset(db_path, dataset_id)
        if dataset is None or dataset["user_id"] != session["user_id"]:
            abort(404)
        return jsonify(store.list_runs(db_path, dataset_id))

    # Scheduled monitoring runs while the server is running. One thread per
    # app, started once; skipped entirely when DATAWATCH_NO_SCHEDULER=1
    # (tests set this so test apps never spawn threads).
    if os.environ.get("DATAWATCH_NO_SCHEDULER") != "1" and not app.config.get(
        "_SCHEDULER_STARTED"
    ):
        monitor.start_scheduler(db_path)
        app.config["_SCHEDULER_STARTED"] = True

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5001,
        debug=os.environ.get("DATAWATCH_DEBUG") == "1",
    )
