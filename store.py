import sqlite3
import json
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
            """
        )


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


def save_run(db_path, dataset_id, filename, result) -> int:
    summary = result.get("summary", {})
    errors = summary.get("errors", 0)
    warnings = summary.get("warnings", 0)

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO runs
            (dataset_id, filename, score, errors, warnings, total_rows, checks_run, column_stats_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                   checks_run, column_stats_json, created_at
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None

        result = dict(row)
        result["column_stats"] = json.loads(result.pop("column_stats_json"))

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
