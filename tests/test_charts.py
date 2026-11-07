from charts import score_trend_svg


def test_empty_runs_returns_empty_string():
    assert score_trend_svg([]) == ""


def test_single_run_draws_dot_and_label():
    svg = score_trend_svg(
        [{"score": 98.7, "created_at": "2026-11-07T09:00:00+00:00"}]
    )
    assert "<svg" in svg and "</svg>" in svg
    assert "98.7" in svg
    assert "2026-11-07" in svg
    assert "<circle" in svg
    assert "<polyline" not in svg


def test_two_runs_draw_a_line():
    svg = score_trend_svg([
        {"score": 90.0, "created_at": "2026-11-07T09:00:00+00:00"},
        {"score": 100.0, "created_at": "2026-11-07T10:00:00+00:00"},
    ])
    assert "<polyline" in svg
    assert "90.0" in svg and "100.0" in svg
    assert svg.count("<circle") == 2
