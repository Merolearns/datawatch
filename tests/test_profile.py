from profile import profile_columns

CONTRACT = {
    "columns": [
        {"name": "id", "type": "integer"},
        {"name": "amount", "type": "number"},
        {"name": "status", "type": "string"},
        {"name": "created_at", "type": "date"},
        {"name": "email", "type": "email"},
    ]
}

ROWS = [
    {"id": "1", "amount": "10.5", "status": "paid", "created_at": "2026-01-02", "email": "a@x.com"},
    {"id": "2", "amount": "20", "status": "paid", "created_at": "2026-01-05", "email": "b@x.com"},
    {"id": "3", "amount": "30", "status": "shipped", "created_at": "2026-01-01", "email": ""},
    {"id": "4", "amount": "bad", "status": "paid", "created_at": "not-a-date", "email": "a@x.com"},
    {"id": "", "amount": "", "status": "", "created_at": "", "email": ""},
]


def test_numeric_min_max_mean_median():
    result = profile_columns(ROWS, CONTRACT)
    assert result["id"]["min"] == 1
    assert result["id"]["max"] == 4
    assert result["id"]["mean"] == 2.5
    assert result["id"]["median"] == 2.5
    # "bad" is ignored for the amount stats; mean is 60.5 / 3.
    assert result["amount"]["min"] == 10.5
    assert result["amount"]["max"] == 30
    assert result["amount"]["mean"] == 20.17
    assert result["amount"]["median"] == 20


def test_null_pct_and_distinct():
    result = profile_columns(ROWS, CONTRACT)
    assert result["id"]["rows"] == 5
    assert result["id"]["nulls"] == 1
    assert result["id"]["null_pct"] == 20.0
    assert result["id"]["distinct"] == 4
    assert result["email"]["nulls"] == 2
    assert result["email"]["null_pct"] == 40.0
    assert result["email"]["distinct"] == 2


def test_top_values_with_counts():
    result = profile_columns(ROWS, CONTRACT)
    assert result["status"]["top_values"] == [
        {"value": "paid", "count": 3},
        {"value": "shipped", "count": 1},
    ]
    assert result["email"]["top_values"] == [
        {"value": "a@x.com", "count": 2},
        {"value": "b@x.com", "count": 1},
    ]


def test_date_earliest_latest():
    result = profile_columns(ROWS, CONTRACT)
    assert result["created_at"]["earliest"] == "2026-01-01"
    assert result["created_at"]["latest"] == "2026-01-05"


def test_missing_column():
    contract = {"columns": [{"name": "ghost", "type": "string"}]}
    assert profile_columns(ROWS, contract) == {"ghost": {"missing": True}}


def test_empty_rows():
    result = profile_columns([], CONTRACT)
    for name in ("id", "amount", "status", "created_at", "email"):
        assert result[name]["rows"] == 0
        assert result[name]["nulls"] == 0
        assert result[name]["null_pct"] == 0.0
        assert result[name]["distinct"] == 0
    assert result["id"]["min"] is None
    assert result["id"]["max"] is None
    assert result["id"]["mean"] is None
    assert result["id"]["median"] is None
    assert result["status"]["top_values"] == []
