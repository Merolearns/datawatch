import csv
import datetime
import io
import math
import re


def parse_csv_text(text: str) -> list[dict[str, str]]:
    if text is None or text.strip() == "":
        return []

    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text), restval="")
    if reader.fieldnames is None:
        return []

    reader.fieldnames = [name.strip() for name in reader.fieldnames]
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("duplicate column names in CSV header")
    return list(reader)


def _type_is_valid(value: str, col_type: str) -> bool:
    if col_type == "string":
        return True
    if col_type == "integer":
        return bool(re.fullmatch(r"-?\d+", value))
    if col_type == "number":
        try:
            return math.isfinite(float(value))
        except ValueError:
            return False
    if col_type == "boolean":
        return value.lower() in {"true", "false", "1", "0", "yes", "no"}
    if col_type == "date":
        try:
            datetime.datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    if col_type == "email":
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))
    return True


def _comparable(value: str, col_type: str):
    if col_type == "integer":
        return int(value)
    if col_type == "number":
        return float(value)
    return value


def _coerce_limit(limit, col_type: str):
    # Compare numeric limits as floats so an integer min of 0.5 stays 0.5;
    # date limits stay ISO strings (lexicographic order matches date order).
    if col_type in ("integer", "number"):
        return float(limit)
    return str(limit)


def _violation(row, column, rule, message, value):
    return {
        "row": row,
        "column": column,
        "rule": rule,
        "severity": "error",
        "message": message,
        "value": value,
    }


def validate(rows: list[dict[str, str]], contract: dict) -> dict:
    violations = []
    checks_run = 0
    column_stats = {}

    columns = contract.get("columns", [])

    for col in columns:
        name = col.get("name")
        if not name:
            continue
        col_type = col.get("type", "string")
        required = col.get("required", False)
        unique = col.get("unique", False)
        minv = col.get("min")
        maxv = col.get("max")
        pattern = col.get("pattern")
        allowed = col.get("allowed") or []

        # Missing column check.
        if not rows or name not in rows[0]:
            checks_run += 1
            violations.append(
                _violation(
                    None,
                    name,
                    "missing_column",
                    f"{name} is missing from the data",
                    None,
                )
            )
            column_stats[name] = {"nulls": 0, "distinct": 0, "violations": 1}
            continue

        seen = {}
        nulls = 0
        distinct_values = set()

        for row_idx, row in enumerate(rows):
            value = row.get(name, "")
            row_num = row_idx + 1

            # Presence / required check.
            checks_run += 1
            if value == "":
                nulls += 1
                if required:
                    violations.append(
                        _violation(
                            row_num,
                            name,
                            "required",
                            f"{name} is required but empty",
                            value,
                        )
                    )
                continue

            # Type check.
            checks_run += 1
            if not _type_is_valid(value, col_type):
                violations.append(
                    _violation(
                        row_num,
                        name,
                        "type",
                        f"{name} must be a valid {col_type}",
                        value,
                    )
                )
                continue

            # Range check.
            if col_type in ("integer", "number", "date") and (
                minv is not None or maxv is not None
            ):
                comparable = _comparable(value, col_type)
                if minv is not None:
                    checks_run += 1
                    if comparable < _coerce_limit(minv, col_type):
                        violations.append(
                            _violation(
                                row_num,
                                name,
                                "range",
                                f"{name} must be at least {minv}",
                                value,
                            )
                        )
                if maxv is not None:
                    checks_run += 1
                    if comparable > _coerce_limit(maxv, col_type):
                        violations.append(
                            _violation(
                                row_num,
                                name,
                                "range",
                                f"{name} must be at most {maxv}",
                                value,
                            )
                        )

            # Pattern check (full match; an invalid pattern is skipped —
            # dataset creation rejects bad patterns, this is defense in depth).
            if pattern:
                checks_run += 1
                try:
                    matched = re.fullmatch(pattern, value) is not None
                except re.error:
                    matched = True
                if not matched:
                    violations.append(
                        _violation(
                            row_num,
                            name,
                            "pattern",
                            f"{name} must match pattern {pattern!r}",
                            value,
                        )
                    )

            # Allowed values check.
            if allowed:
                checks_run += 1
                if value not in allowed:
                    violations.append(
                        _violation(
                            row_num,
                            name,
                            "allowed",
                            f"{name} must be one of {allowed}",
                            value,
                        )
                    )

            # Uniqueness check.
            if unique:
                checks_run += 1
                if value in seen:
                    first_row = seen[value] + 1
                    violations.append(
                        _violation(
                            row_num,
                            name,
                            "unique",
                            (
                                f"{name} must be unique; value {value!r} "
                                f"first appeared in row {first_row}"
                            ),
                            value,
                        )
                    )
                else:
                    seen[value] = row_idx

            distinct_values.add(value)

        col_violations = sum(1 for v in violations if v["column"] == name)
        column_stats[name] = {
            "nulls": nulls,
            "distinct": len(distinct_values),
            "violations": col_violations,
        }

    errors = len(violations)
    total_rows = len(rows)

    if total_rows == 0 and columns:
        score = 0.0
    else:
        score = round(100 * (1 - errors / max(1, checks_run)), 1)
        if score < 0:
            score = 0.0

    return {
        "score": score,
        "passed": errors == 0,
        "total_rows": total_rows,
        "checks_run": checks_run,
        "violations": violations,
        "column_stats": column_stats,
        "summary": {"errors": errors, "warnings": 0},
    }
