import pytest

from engine import validate, parse_csv_text


CONTRACT = {
    "name": "orders",
    "columns": [
        {
            "name": "order_id",
            "type": "integer",
            "required": True,
            "unique": True,
            "min": None,
            "max": None,
            "pattern": None,
            "allowed": None,
        },
        {
            "name": "customer_email",
            "type": "email",
            "required": True,
            "unique": False,
            "min": None,
            "max": None,
            "pattern": None,
            "allowed": None,
        },
        {
            "name": "amount",
            "type": "number",
            "required": True,
            "unique": False,
            "min": 0,
            "max": None,
            "pattern": None,
            "allowed": None,
        },
        {
            "name": "status",
            "type": "string",
            "required": True,
            "unique": False,
            "min": None,
            "max": None,
            "pattern": None,
            "allowed": ["pending", "paid", "shipped", "refunded"],
        },
        {
            "name": "created_at",
            "type": "date",
            "required": True,
            "unique": False,
            "min": None,
            "max": None,
            "pattern": None,
            "allowed": None,
        },
    ],
}


def test_valid_rows_score_100():
    rows = [
        {
            "order_id": "1",
            "customer_email": "alice@example.com",
            "amount": "10.5",
            "status": "paid",
            "created_at": "2025-01-15",
        },
        {
            "order_id": "2",
            "customer_email": "bob@example.com",
            "amount": "20",
            "status": "pending",
            "created_at": "2025-02-20",
        },
    ]
    result = validate(rows, CONTRACT)
    assert result["score"] == 100.0
    assert result["passed"] is True
    assert result["total_rows"] == 2
    assert len(result["violations"]) == 0
    assert result["summary"]["errors"] == 0
    assert result["summary"]["warnings"] == 0


def test_required_empty_email():
    rows = [
        {
            "order_id": "1",
            "customer_email": "",
            "amount": "10",
            "status": "paid",
            "created_at": "2025-01-15",
        }
    ]
    result = validate(rows, CONTRACT)
    required = [
        v for v in result["violations"]
        if v["column"] == "customer_email" and v["rule"] == "required"
    ]
    assert len(required) == 1
    assert required[0]["row"] == 1


def test_bad_email_type_violation():
    rows = [
        {
            "order_id": "1",
            "customer_email": "not-an-email",
            "amount": "10",
            "status": "paid",
            "created_at": "2025-01-15",
        }
    ]
    result = validate(rows, CONTRACT)
    bad_email = [
        v for v in result["violations"]
        if v["column"] == "customer_email" and v["rule"] == "type"
    ]
    assert len(bad_email) == 1
    assert bad_email[0]["row"] == 1


def test_negative_amount_range_violation():
    rows = [
        {
            "order_id": "1",
            "customer_email": "alice@example.com",
            "amount": "-5",
            "status": "paid",
            "created_at": "2025-01-15",
        }
    ]
    result = validate(rows, CONTRACT)
    range_violations = [
        v for v in result["violations"]
        if v["column"] == "amount" and v["rule"] == "range"
    ]
    assert len(range_violations) == 1
    assert range_violations[0]["row"] == 1


def test_non_numeric_amount_type_only():
    rows = [
        {
            "order_id": "1",
            "customer_email": "alice@example.com",
            "amount": "abc",
            "status": "paid",
            "created_at": "2025-01-15",
        }
    ]
    result = validate(rows, CONTRACT)
    amount_violations = [v for v in result["violations"] if v["column"] == "amount"]
    type_violations = [v for v in amount_violations if v["rule"] == "type"]
    range_violations = [v for v in amount_violations if v["rule"] == "range"]
    assert len(type_violations) == 1
    assert len(range_violations) == 0


def test_status_outside_allowed():
    rows = [
        {
            "order_id": "1",
            "customer_email": "alice@example.com",
            "amount": "10",
            "status": "cancelled",
            "created_at": "2025-01-15",
        }
    ]
    result = validate(rows, CONTRACT)
    allowed_violations = [
        v for v in result["violations"]
        if v["column"] == "status" and v["rule"] == "allowed"
    ]
    assert len(allowed_violations) == 1
    assert allowed_violations[0]["row"] == 1


def test_duplicate_order_id():
    rows = [
        {
            "order_id": "42",
            "customer_email": "alice@example.com",
            "amount": "10",
            "status": "paid",
            "created_at": "2025-01-15",
        },
        {
            "order_id": "42",
            "customer_email": "bob@example.com",
            "amount": "20",
            "status": "pending",
            "created_at": "2025-02-20",
        },
    ]
    result = validate(rows, CONTRACT)
    unique_violations = [
        v for v in result["violations"]
        if v["column"] == "order_id" and v["rule"] == "unique"
    ]
    assert len(unique_violations) == 1
    assert unique_violations[0]["row"] == 2


def test_bad_date_type_violation():
    rows = [
        {
            "order_id": "1",
            "customer_email": "alice@example.com",
            "amount": "10",
            "status": "paid",
            "created_at": "2026-13-40",
        }
    ]
    result = validate(rows, CONTRACT)
    date_violations = [
        v for v in result["violations"]
        if v["column"] == "created_at" and v["rule"] == "type"
    ]
    assert len(date_violations) == 1
    assert date_violations[0]["row"] == 1


def test_missing_column():
    full_row = {
        "order_id": "1",
        "customer_email": "alice@example.com",
        "amount": "10",
        "status": "paid",
        "created_at": "2025-01-15",
    }
    rows = [{k: v for k, v in full_row.items() if k != "amount"}]
    result = validate(rows, CONTRACT)
    missing = [
        v for v in result["violations"] if v["rule"] == "missing_column"
    ]
    assert len(missing) == 1
    assert missing[0]["row"] is None
    assert result["score"] < 100.0


def test_empty_rows_zero_score():
    result = validate([], CONTRACT)
    assert result["score"] == 0.0
    assert result["passed"] is False
    assert result["total_rows"] == 0


def test_column_stats_counts():
    rows = [
        {
            "order_id": "1",
            "customer_email": "a@example.com",
            "amount": "10",
            "status": "paid",
            "created_at": "2025-01-01",
        },
        {
            "order_id": "2",
            "customer_email": "",
            "amount": "20",
            "status": "paid",
            "created_at": "2025-01-02",
        },
        {
            "order_id": "1",
            "customer_email": "a@example.com",
            "amount": "30",
            "status": "paid",
            "created_at": "2025-01-03",
        },
    ]
    result = validate(rows, CONTRACT)
    stats = result["column_stats"]
    assert stats["order_id"]["distinct"] == 2
    assert stats["order_id"]["nulls"] == 0
    assert stats["customer_email"]["nulls"] == 1
    assert "distinct" in stats["customer_email"]


def test_parse_csv_text():
    text = "order_id,status\n1,paid\n2,pending\n"
    rows = parse_csv_text(text)
    assert len(rows) == 2
    assert rows[0] == {"order_id": "1", "status": "paid"}
    assert rows[1] == {"order_id": "2", "status": "pending"}
    for row in rows:
        for value in row.values():
            assert isinstance(value, str)
    assert parse_csv_text("") == []
    assert parse_csv_text("   ") == []


def test_pattern_rule():
    contract = {
        "name": "codes",
        "columns": [
            {
                "name": "code",
                "type": "string",
                "required": True,
                "unique": False,
                "min": None,
                "max": None,
                "pattern": r"^[A-Z]{3}-\d+$",
                "allowed": None,
            }
        ],
    }
    good = [{"code": "ABC-123"}]
    bad = [{"code": "abc-123"}]
    good_result = validate(good, contract)
    bad_result = validate(bad, contract)
    assert len(good_result["violations"]) == 0
    pattern_violations = [
        v for v in bad_result["violations"]
        if v["column"] == "code" and v["rule"] == "pattern"
    ]
    assert len(pattern_violations) == 1
    assert pattern_violations[0]["row"] == 1




def test_invalid_pattern_does_not_crash():
    contract = {"name": "t", "columns": [
        {"name": "code", "type": "string", "required": True, "unique": False,
         "min": None, "max": None, "pattern": "[invalid", "allowed": None}]}
    result = validate([{"code": "ABC"}], contract)
    assert result["passed"] is True  # bad pattern is skipped, never a crash


def test_nan_and_inf_rejected_as_numbers():
    rows = [{"amount": "nan"}, {"amount": "inf"}, {"amount": "12.5"}]
    contract = {"name": "t", "columns": [
        {"name": "amount", "type": "number", "required": True, "unique": False,
         "min": None, "max": None, "pattern": None, "allowed": None}]}
    result = validate(rows, contract)
    types = [v for v in result["violations"] if v["rule"] == "type"]
    assert len(types) == 2


def test_pattern_matches_whole_value():
    contract = {"name": "t", "columns": [
        {"name": "code", "type": "string", "required": True, "unique": False,
         "min": None, "max": None, "pattern": "ABC", "allowed": None}]}
    assert validate([{"code": "ABC"}], contract)["passed"] is True
    bad = validate([{"code": "XABCX"}], contract)
    assert len([v for v in bad["violations"] if v["rule"] == "pattern"]) == 1


def test_date_min_max_range():
    contract = {"name": "t", "columns": [
        {"name": "d", "type": "date", "required": True, "unique": False,
         "min": "2026-01-01", "max": "2026-12-31", "pattern": None, "allowed": None}]}
    result = validate([{"d": "2025-06-01"}, {"d": "2026-06-01"}], contract)
    ranges = [v for v in result["violations"] if v["rule"] == "range"]
    assert len(ranges) == 1
    assert ranges[0]["row"] == 1


def test_parse_csv_text_rejects_duplicate_headers():
    with pytest.raises(ValueError):
        parse_csv_text("a,a\n1,2\n")


def test_parse_csv_text_strips_bom():
    rows = parse_csv_text("\ufeffid,name\n1,Ann\n")
    assert rows == [{"id": "1", "name": "Ann"}]
