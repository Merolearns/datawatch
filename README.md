# DataWatch

A data observability platform: define a **data contract** (the schema and rules
a dataset should follow), run it against a CSV upload, and get a violation
report with a quality score — then watch that score over time.

This is Phase 1 of a larger build. It covers contracts, validation runs, run
history with a score trend, and accounts. Scheduled monitoring, alerts, and
column profiling are planned for Phase 2.

## What it does

- **Contract builder** — define columns with a type (`string`, `integer`,
  `number`, `boolean`, `date`, `email`), required/unique flags, min/max,
  regex pattern, and allowed values. Duplicate column names, bad numbers,
  and invalid regexes are rejected in the form.
- **Validation engine** — checks every row against the contract: required
  values, types, ranges, patterns, allowed values, uniqueness, and missing
  columns. Every violation names its row, column, rule, and offending value.
- **Run history** — every run is stored in SQLite with its score, error
  count, and violations. The dataset page shows a score-trend chart and a
  run history table.
- **Accounts** — sign up and log in; each user only sees their own datasets
  and runs. Passwords are hashed with PBKDF2-HMAC-SHA256.
- **Upload, paste, or sample** — run a check from a CSV file, pasted CSV
  text, or one of the two bundled samples. Violations download as CSV, and
  run history is available as JSON.

## Example output

The bundled samples are a 60-row orders file (`order_id`, `customer_email`,
`amount`, `status`, `created_at`) run against the orders contract:

| File | Score | Checks | Errors |
| --- | --- | --- | --- |
| `sample_orders_clean.csv` | 100.0/100 (Pass) | 780 | 0 |
| `sample_orders_dirty.csv` | 98.7/100 (Fail) | 775 | 10 |

The dirty file's 10 violations: 2 missing required values, 4 type errors
(bad email, non-numeric amount, bad date, non-integer order id), 1 range
error (negative amount), 2 allowed-value errors, 1 duplicate order id.

![Dashboard](images/02-dashboard.png)
![Contract builder](images/03-builder.png)
![Run report](images/05-run-dirty.png)
![Dataset page with score trend](images/06-dataset-full.png)

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5001, sign up, create a dataset (the builder comes
prefilled with the orders contract), and run the dirty sample.

Set `DATAWATCH_SECRET` to a long random value in production; without it the
app generates an ephemeral secret at startup and sessions do not survive a
restart. Set `DATAWATCH_DEBUG=1` to enable Flask debug mode (off by default).
Uploads are limited to 5 MB.

## API

- `GET /api/datasets/<id>/runs` — JSON list of runs for a dataset (login
  required; 401 when logged out).

## Tests

```bash
python -m pytest tests/ -q
```

49 tests: engine rules (required/type/range/pattern/allowed/unique/missing
column, scoring, BOM and duplicate-header handling), store CRUD, auth
hashing, the SVG trend chart, and the Flask routes end to end (register,
login, dataset creation, sample runs, export, per-user isolation).

## Project layout

```
app.py            Flask app: routes, auth, CSRF, upload handling
engine.py         contract validation (stdlib only)
store.py          SQLite persistence (datasets, runs, violations)
auth.py           PBKDF2 password hashing
charts.py         SVG score-trend chart
templates/        Jinja pages (dashboard, builder, dataset, run, auth)
static/style.css  dark amber/teal theme
data/             bundled clean/dirty order samples + generator
tests/            pytest suite
```

## Roadmap (Phase 2)

Scheduled/repeat monitoring with snapshot comparison, alerts when a run's
score drops below a threshold, per-column profiling, and a live deployment.
