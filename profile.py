"""Per-column profiling for a validated CSV run.

Pure functions, stdlib only. Never raises on weird values — anything that
cannot be parsed is skipped (the validator already flags it).
"""

import datetime
from collections import Counter


def _parse_number(value, col_type):
    try:
        if col_type == "integer":
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return round(float(ordered[mid]), 2)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 2)


def profile_columns(rows, contract):
    """Return {column_name: stats} for each column in the contract."""
    result = {}
    columns = contract.get("columns", []) if contract else []

    for col in columns:
        name = col.get("name")
        if not name:
            continue
        col_type = col.get("type", "string")

        # A column that never appears in the data gets a marker and nothing else.
        if rows and name not in rows[0]:
            result[name] = {"missing": True}
            continue

        total = len(rows)
        nulls = 0
        values = []
        for row in rows:
            value = row.get(name, "")
            if value == "":
                nulls += 1
            else:
                values.append(value)

        stats = {
            "rows": total,
            "nulls": nulls,
            "null_pct": round(100 * nulls / total, 1) if total else 0.0,
            "distinct": len(set(values)),
        }

        if col_type in ("integer", "number"):
            numbers = [
                n for n in (_parse_number(v, col_type) for v in values) if n is not None
            ]
            if numbers:
                stats["min"] = min(numbers)
                stats["max"] = max(numbers)
                stats["mean"] = round(sum(numbers) / len(numbers), 2)
                stats["median"] = _median(numbers)
            else:
                stats["min"] = None
                stats["max"] = None
                stats["mean"] = None
                stats["median"] = None
        elif col_type == "date":
            dates = [d for d in (_parse_date(v) for v in values) if d is not None]
            if dates:
                stats["earliest"] = min(dates).isoformat()
                stats["latest"] = max(dates).isoformat()
            else:
                stats["earliest"] = None
                stats["latest"] = None
        else:
            stats["top_values"] = [
                {"value": value, "count": count}
                for value, count in Counter(values).most_common(5)
            ]

        result[name] = stats

    return result
