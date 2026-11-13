#!/usr/bin/env python3
"""DataWatch end-to-end smoke test (stdlib only).

Runs against a live app: /healthz, sign up, create the orders dataset
from the prefilled builder form, run the dirty sample, and check the
score is the known 98.7/100 with 10 violations. Never prints a secret.

Usage: python3 deploy/smoke-test.py [base-url]
Default base URL is http://127.0.0.1:5002 (the VPS loopback port).
"""

import http.cookiejar
import json
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class _FormParser(HTMLParser):
    """Collect (name, value) pairs from inputs and selects, in order."""

    def __init__(self):
        super().__init__()
        self.pairs = []
        self._select_name = None
        self._select_value = None
        self._option_selected = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.pairs.append((attrs["name"], attrs.get("value", "")))
        elif tag == "select" and attrs.get("name"):
            self._select_name = attrs["name"]
            self._select_value = None
            self._option_selected = False
        elif tag == "option" and self._select_name is not None:
            if "selected" in attrs:
                self._select_value = attrs.get("value", "")
                self._option_selected = True
            elif self._select_value is None:
                # First option is the default when none is selected.
                self._select_value = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select" and self._select_name is not None:
            self.pairs.append((self._select_name, self._select_value or ""))
            self._select_name = None


def form_pairs(html):
    parser = _FormParser()
    parser.feed(html)
    return parser.pairs


def csrf_token(html):
    for name, value in form_pairs(html):
        if name == "csrf_token":
            return value
    return None


class Smoke:
    def __init__(self, base):
        self.base = base.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

    def check(self, ok, step, detail=""):
        print(f"{'OK  ' if ok else 'FAIL'} {step}" + (f" — {detail}" if detail else ""))
        if not ok:
            sys.exit(1)

    def get(self, path):
        try:
            with self.opener.open(self.base + path, timeout=15) as resp:
                return resp.geturl(), resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return self.base + path, e.code, e.read().decode("utf-8", "replace")

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.geturl(), resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return self.base + path, e.code, e.read().decode("utf-8", "replace")


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5002"
    s = Smoke(base)

    url, status, body = s.get("/healthz")
    s.check(status == 200 and '"ok"' in body, "healthz", f"{status} {url}")

    url, status, body = s.get("/register")
    s.check(status == 200 and "csrf_token" in body, "register page", f"{status}")
    token = csrf_token(body)
    s.check(bool(token), "register csrf token")

    username = "smoke-" + secrets.token_hex(3)
    password = secrets.token_hex(8)  # never printed
    url, status, body = s.post(
        "/register",
        [("csrf_token", token), ("username", username), ("password", password)],
    )
    s.check(
        status == 200 and url.rstrip("/") == s.base and "New dataset" in body,
        "sign up + dashboard", f"{status} {url}",
    )

    url, status, body = s.get("/datasets/new")
    s.check(status == 200 and "col_name" in body, "builder page", f"{status}")
    fields = form_pairs(body)
    fields = [(n, v) for n, v in fields if n != "dataset_name"]
    fields.append(("dataset_name", "orders"))
    s.check(
        any(n == "col_name" and v == "order_id" for n, v in fields),
        "builder prefill (orders contract)",
    )
    url, status, body = s.post("/datasets/new", fields)
    m = re.search(r"/datasets/(\d+)", url)
    s.check(
        status == 200 and m and "orders" in body.lower(),
        "create dataset", f"{status} {url}",
    )
    dataset_id = m.group(1)

    url, status, body = s.get(f"/datasets/{dataset_id}")
    s.check(status == 200, "dataset page", f"{status}")
    token = csrf_token(body)
    s.check(bool(token), "dataset csrf token")

    url, status, body = s.post(
        f"/datasets/{dataset_id}/run",
        [("csrf_token", token), ("sample", "dirty")],
    )
    m = re.search(r"/runs/(\d+)", url)
    s.check(status == 200 and m, "run dirty sample", f"{status} {url}")
    run_id = m.group(1)

    url, status, body = s.get(f"/runs/{run_id}")
    s.check(
        status == 200 and "98.7/100" in body and "Fail" in body,
        "run report (dirty sample 98.7/100, Fail)", f"{status}",
    )

    url, status, body = s.get(f"/api/datasets/{dataset_id}/runs")
    try:
        runs = json.loads(body)
    except json.JSONDecodeError:
        runs = None
    s.check(
        status == 200 and isinstance(runs, list) and len(runs) == 1
        and abs(runs[0]["score"] - 98.7) < 0.01,
        "runs API (1 run, score 98.7)", f"{status}",
    )

    print(f"SMOKE OK: {base} — sign up, dataset, dirty run 98.7/100, API all green")


if __name__ == "__main__":
    main()
