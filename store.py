import json
import sqlite3
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY,
                dataset_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                score REAL NOT NULL,
                errors INTEGER NOT NULL,
                warnings INTEGER NOT NULL,
                total_rows INTEGER NOT NULL,
                checks_run INTEGER NOT NULL,
                column_stats_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                row_no INTEGER,
                column_name TEXT,
                rule TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY,
                dataset_id INTEGER UNIQUE NOT NULL,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL DEFAULT 0,
                last_run_at TEXT,
                next_run_at TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                dataset_id INTEGER PRIMARY KEY,
                score_below REAL,
                errors_above INTEGER
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY,
                dataset_id INTEGER NOT NULL,
                run_id INTEGER,
                rule TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Migrate databases created before Phase 2: add the new columns
        # when they are missing. Existing data is left untouched.
        _add_column_if_missing(conn, "runs", "profile_json", "TEXT")
        _add_column_if_missing(
            conn, "runs", "source", "TEXT NOT NULL DEFAULT 'manual'"
        )
        _add_column_if_missing(conn, "datasets", "last_csv_text", "TEXT")
        _add_column_if_missing(conn, "datasets", "last_filename", "TEXT")


def _add_column_if_missing(conn, table, column, ddl):
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def create_user(db_path, username, password_hash) -> int:
    with _connect(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _utc_now()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("username taken")


def get_user(db_path, username):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def create_dataset(db_path, user_id, name, contract) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO datasets (user_id, name, contract_json, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, json.dumps(contract), _utc_now()),
        )
        return cur.lastrowid


def list_datasets(db_path, user_id):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                d.id,
                d.name,
                d.created_at,
                COUNT(r.id) AS run_count,
                (
                    SELECT r2.score
                    FROM runs r2
                    WHERE r2.dataset_id = d.id
                    ORDER BY r2.created_at DESC, r2.id DESC
                    LIMIT 1
                ) AS last_score
            FROM datasets d
            LEFT JOIN runs r ON r.dataset_id = d.id
            WHERE d.user_id = ?
            GROUP BY d.id
            ORDER BY d.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_dataset(db_path, dataset_id):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, user_id, name, contract_json, created_at FROM datasets WHERE id = ?",
            (dataset_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["contract"] = json.loads(result.pop("contract_json"))
        return result


def save_run(db_path, dataset_id, filename, result, profile=None, source="manual") -> int:
    summary = result.get("summary", {})
    errors = summary.get("errors", 0)
    warnings = summary.get("warnings", 0)

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO runs
            (dataset_id, filename, score, errors, warnings, total_rows, checks_run, column_stats_json, profile_json, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                filename,
                result["score"],
                errors,
                warnings,
                result["total_rows"],
                result["checks_run"],
                json.dumps(result["column_stats"]),
                json.dumps(profile) if profile is not None else None,
                source,
                _utc_now(),
            ),
        )
        run_id = cur.lastrowid

        for v in result.get("violations", []):
            conn.execute(
                """
                INSERT INTO violations
                (run_id, row_no, column_name, rule, severity, message, value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    v.get("row"),
                    v.get("column"),
                    v["rule"],
                    v["severity"],
                    v["message"],
                    v.get("value"),
                ),
            )

        return run_id


def list_runs(db_path, dataset_id, limit=20):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, score, errors, warnings, total_rows, created_at
            FROM runs
            WHERE dataset_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (dataset_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_run(db_path, run_id):
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, dataset_id, filename, score, errors, warnings, total_rows,
                   checks_run, column_stats_json, profile_json, source, created_at
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None

        result = dict(row)
        result["column_stats"] = json.loads(result.pop("column_stats_json"))
        profile_json = result.pop("profile_json")
        result["profile"] = json.loads(profile_json) if profile_json else None

        vrows = conn.execute(
            """
            SELECT row_no AS row, column_name AS column, rule, severity, message, value
            FROM violations
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        result["violations"] = [dict(v) for v in vrows]
        return result


def latest_runs(db_path, user_id, limit=10):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.dataset_id, d.name AS dataset_name, r.filename,
                   r.score, r.errors, r.warnings, r.total_rows, r.created_at
            FROM runs r
            JOIN datasets d ON d.id = r.dataset_id
            WHERE d.user_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def set_last_csv(db_path, dataset_id, text, filename):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE datasets SET last_csv_text = ?, last_filename = ? WHERE id = ?",
            (text, filename, dataset_id),
        )


def get_last_csv(db_path, dataset_id):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_csv_text, last_filename FROM datasets WHERE id = ?",
            (dataset_id,),
        ).fetchone()
        if not row or row["last_csv_text"] is None:
            return None
        return {"text": row["last_csv_text"], "filename": row["last_filename"]}


def upsert_schedule(db_path, dataset_id, interval_minutes, enabled):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, next_run_at FROM schedules WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        if row is None:
            next_run_at = None
            if enabled:
                next_run_at = _add_minutes(_utc_now(), interval_minutes)
            cur = conn.execute(
                """
                INSERT INTO schedules
                (dataset_id, interval_minutes, enabled, next_run_at)
                VALUES (?, ?, ?, ?)
                """,
                (dataset_id, interval_minutes, 1 if enabled else 0, next_run_at),
            )
            schedule_id = cur.lastrowid
        else:
            schedule_id = row["id"]
            next_run_at = row["next_run_at"]
            if enabled and next_run_at is None:
                next_run_at = _add_minutes(_utc_now(), interval_minutes)
            conn.execute(
                """
                UPDATE schedules
                SET interval_minutes = ?, enabled = ?, next_run_at = ?
                WHERE id = ?
                """,
                (interval_minutes, 1 if enabled else 0, next_run_at, schedule_id),
            )
    return get_schedule(db_path, dataset_id)


def _add_minutes(iso, minutes):
    from datetime import timedelta

    when = datetime.fromisoformat(iso)
    return (when + timedelta(minutes=minutes)).isoformat()


def get_schedule(db_path, dataset_id):
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, dataset_id, interval_minutes, enabled, last_run_at, next_run_at
            FROM schedules WHERE dataset_id = ?
            """,
            (dataset_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result


def list_due_schedules(db_path, now_iso):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, dataset_id, interval_minutes, enabled, last_run_at, next_run_at
            FROM schedules
            WHERE enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY next_run_at ASC, id ASC
            """,
            (now_iso,),
        ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["enabled"] = bool(result["enabled"])
            results.append(result)
        return results


def touch_schedule(db_path, schedule_id, last_run_at, next_run_at):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE schedules SET last_run_at = ?, next_run_at = ? WHERE id = ?",
            (last_run_at, next_run_at, schedule_id),
        )


def set_alert_rules(db_path, dataset_id, score_below, errors_above):
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_rules (dataset_id, score_below, errors_above)
            VALUES (?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                score_below = excluded.score_below,
                errors_above = excluded.errors_above
            """,
            (dataset_id, score_below, errors_above),
        )


def get_alert_rules(db_path, dataset_id):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT score_below, errors_above FROM alert_rules WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        if not row:
            return {"score_below": None, "errors_above": None}
        return {"score_below": row["score_below"], "errors_above": row["errors_above"]}


def create_alert(db_path, dataset_id, run_id, rule, message) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts
            (dataset_id, run_id, rule, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dataset_id, run_id, rule, message, _utc_now()),
        )
        return cur.lastrowid


def list_alerts(db_path, user_id, limit=50):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.dataset_id, a.run_id, a.rule, a.message,
                   a.created_at, a.acknowledged, d.name AS dataset_name
            FROM alerts a
            JOIN datasets d ON d.id = a.dataset_id
            WHERE d.user_id = ?
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["acknowledged"] = bool(result["acknowledged"])
            results.append(result)
        return results


def unread_alert_count(db_path, user_id) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM alerts a
            JOIN datasets d ON d.id = a.dataset_id
            WHERE d.user_id = ? AND a.acknowledged = 0
            """,
            (user_id,),
        ).fetchone()
        return row["n"]


def acknowledge_alert(db_path, alert_id, user_id) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE alerts SET acknowledged = 1
            WHERE id = ? AND dataset_id IN (
                SELECT id FROM datasets WHERE user_id = ?
            )
            """,
            (alert_id, user_id),
        )
        return cur.rowcount > 0
