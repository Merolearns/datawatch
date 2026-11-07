from datetime import datetime


def score_trend_svg(runs, width=640, height=180):
    """Return an SVG trend line for quality scores over time."""
    if not runs:
        return ""

    def parse(iso):
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    dates = [parse(r["created_at"]) for r in runs]
    scores = [r["score"] for r in runs]
    n = len(runs)

    pad_left = 40
    pad_right = 40
    pad_top = 20
    pad_bottom = 30
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    t0 = dates[0].timestamp()
    t1 = dates[-1].timestamp()
    span = (t1 - t0) or 1

    points = []
    for d, s in zip(dates, scores):
        x = pad_left + (plot_w / 2 if n == 1 else (d.timestamp() - t0) / span * plot_w)
        y = pad_top + plot_h * (1 - s / 100)
        points.append((x, y))

    parts = []
    for val in (0, 50, 100):
        gy = pad_top + plot_h * (1 - val / 100)
        parts.append(
            f'<line x1="{pad_left}" y1="{gy}" x2="{width - pad_right}" y2="{gy}" '
            'stroke="#26303d" stroke-width="1"/>'
        )

    if n > 1:
        pts = " ".join(f"{x},{y}" for x, y in points)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="#e8a33d" stroke-width="2"/>'
        )

    for (x, y), run in zip(points, runs):
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#2dd4bf"/>')
        parts.append(
            f'<text x="{x}" y="{y - 8}" text-anchor="middle" font-size="11" '
            f'font-family="sans-serif" fill="#e6edf3">{run["score"]}</text>'
        )

    if n == 1:
        x, _ = points[0]
        parts.append(
            f'<text x="{x}" y="{height - 8}" text-anchor="middle" font-size="11" '
            f'font-family="sans-serif" fill="#93a1b3">{runs[0]["created_at"][:10]}</text>'
        )
    else:
        for idx in (0, -1):
            x, _ = points[idx]
            parts.append(
                f'<text x="{x}" y="{height - 8}" text-anchor="middle" font-size="11" '
                f'font-family="sans-serif" fill="#93a1b3">{runs[idx]["created_at"][:10]}</text>'
            )

    content = "\n  ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n  {content}\n</svg>'
    )
