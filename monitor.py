"""Shared run execution and the scheduled-monitoring loop.

The manual route and the scheduler both run checks through execute_run, so a
re-check behaves exactly like a manual run.
"""

import datetime
import profile
import sys
import threading
import time

import engine
import store


def execute_run(db_path, dataset, text, filename, source="manual"):
    """Run the contract against CSV text and save everything.

    Returns (run_id, validate result). A ValueError from parsing propagates
    to the caller.
    """
    rows = engine.parse_csv_text(text)
    result = engine.validate(rows, dataset["contract"])
    columns = profile.profile_columns(rows, dataset["contract"])
    run_id = store.save_run(
        db_path, dataset["id"], filename, result, profile=columns, source=source
    )
    store.set_last_csv(db_path, dataset["id"], text, filename)
    evaluate_alerts(db_path, dataset, result, run_id)
    return run_id, result


def evaluate_alerts(db_path, dataset, result, run_id):
    """Create alerts for any rule the run broke. Returns the created alerts."""
    rules = store.get_alert_rules(db_path, dataset["id"])
    created = []
    run = store.get_run(db_path, run_id)
    filename = run["filename"] if run else dataset["name"]

    score_below = rules.get("score_below")
    if score_below is not None and result["score"] < score_below:
        message = (
            f"Score {result['score']} fell below {score_below} on {filename}"
        )
        alert_id = store.create_alert(
            db_path, dataset["id"], run_id, "score_below", message
        )
        created.append(
            {
                "id": alert_id,
                "dataset_id": dataset["id"],
                "run_id": run_id,
                "rule": "score_below",
                "message": message,
                "acknowledged": 0,
            }
        )

    errors_above = rules.get("errors_above")
    if errors_above is not None and result["summary"]["errors"] > errors_above:
        errors = result["summary"]["errors"]
        message = (
            f"{errors} errors exceeded the limit of {errors_above} on {filename}"
        )
        alert_id = store.create_alert(
            db_path, dataset["id"], run_id, "errors_above", message
        )
        created.append(
            {
                "id": alert_id,
                "dataset_id": dataset["id"],
                "run_id": run_id,
                "rule": "errors_above",
                "message": message,
                "acknowledged": 0,
            }
        )

    return created


def _add_minutes(iso, minutes):
    when = datetime.datetime.fromisoformat(iso)
    return (when + datetime.timedelta(minutes=minutes)).isoformat()


def run_due_checks(db_path, now=None):
    """Run every due schedule once. Returns the runs actually made."""
    if now is None:
        now = store._utc_now()
    made = []

    for schedule in store.list_due_schedules(db_path, now):
        dataset_id = schedule["dataset_id"]
        interval = schedule["interval_minutes"]
        base = schedule["next_run_at"] or now
        try:
            stored = store.get_last_csv(db_path, dataset_id)
            if stored is None:
                # Nothing to re-check yet; move the next run forward so the
                # schedule does not spin every tick.
                store.touch_schedule(
                    db_path,
                    schedule["id"],
                    schedule["last_run_at"],
                    _add_minutes(base, interval),
                )
                continue
            dataset = store.get_dataset(db_path, dataset_id)
            run_id, _result = execute_run(
                db_path, dataset, stored["text"], stored["filename"],
                source="scheduled",
            )
            store.touch_schedule(
                db_path, schedule["id"], now, _add_minutes(now, interval)
            )
            made.append({"dataset_id": dataset_id, "run_id": run_id})
        except ValueError:
            # Bad stored CSV: record nothing, just move the next run forward.
            try:
                store.touch_schedule(
                    db_path,
                    schedule["id"],
                    schedule["last_run_at"],
                    _add_minutes(base, interval),
                )
            except Exception:
                pass
        except Exception as exc:  # one dataset must not stop the others
            print(
                f"datawatch scheduler: skipping dataset {dataset_id}: {exc}",
                file=sys.stderr,
            )
            try:
                store.touch_schedule(
                    db_path,
                    schedule["id"],
                    schedule["last_run_at"],
                    _add_minutes(base, interval),
                )
            except Exception:
                pass

    return made


def start_scheduler(db_path, interval_seconds=60):
    """Start a daemon thread that runs due checks forever. Never raises."""

    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                run_due_checks(db_path)
            except Exception as exc:
                print(f"datawatch scheduler: {exc}", file=sys.stderr)

    thread = threading.Thread(target=_loop, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        print(f"datawatch scheduler: could not start: {exc}", file=sys.stderr)
    return thread
