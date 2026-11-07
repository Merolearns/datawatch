#!/usr/bin/env python3
"""Generate the bundled DataWatch sample CSVs (deterministic, no randomness).

- sample_orders_clean.csv: 60 valid rows against the orders contract.
- sample_orders_dirty.csv: same shape with 10 planted violations:
  row 5: empty customer_email (required)
  row 8: "not-an-email" (email type)
  row 12: amount -25.50 (range, min 0)
  row 15: amount "abc" (number type)
  row 20: order_id duplicated from row 19 (unique)
  row 25: status "lost" (allowed values)
  row 30: created_at "2026-13-40" (date type)
  row 35: empty amount (required)
  row 40: status "pending " with a trailing space (allowed values)
  row 45: order_id "10X7" (integer type)
Run from the project root: python3 data/gen_samples.py
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATUSES = ["pending", "paid", "shipped", "refunded"]


def base_rows():
    rows = []
    for i in range(60):
        rows.append({
            "order_id": str(1001 + i),
            "customer_email": f"user{1001 + i}@example.com",
            "amount": f"{9.99 + (i * 7.13) % 480:.2f}",
            "status": STATUSES[i % 4],
            "created_at": f"2026-01-{(i % 28) + 1:02d}",
        })
    return rows


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["order_id", "customer_email", "amount", "status", "created_at"])
        w.writeheader()
        w.writerows(rows)


def main():
    clean = base_rows()
    write(os.path.join(HERE, "sample_orders_clean.csv"), clean)

    dirty = base_rows()
    dirty[4]["customer_email"] = ""
    dirty[7]["customer_email"] = "not-an-email"
    dirty[11]["amount"] = "-25.50"
    dirty[14]["amount"] = "abc"
    dirty[19]["order_id"] = dirty[18]["order_id"]
    dirty[24]["status"] = "lost"
    dirty[29]["created_at"] = "2026-13-40"
    dirty[34]["amount"] = ""
    dirty[39]["status"] = "pending "
    dirty[44]["order_id"] = "10X7"
    write(os.path.join(HERE, "sample_orders_dirty.csv"), dirty)
    print("wrote sample_orders_clean.csv (60 rows) and sample_orders_dirty.csv (60 rows, 10 planted violations)")


if __name__ == "__main__":
    main()
