#!/usr/bin/env python3
"""
test_load_balancer.py
=====================
Sends N requests to the Nginx load balancer and reports which backend
(Web01 or Web02) handled each one, using the X-Served-By response header.

The Nginx config in nginx/lb01.conf adds:
    add_header  X-Served-By  $upstream_addr  always;

Usage
-----
    python scripts/test_load_balancer.py --lb <LB_IP_OR_DOMAIN> [--requests 6] [--endpoint /api/forecast?model=prophet&hours=24]

Examples
--------
    # Quick 6-request round-robin check
    python scripts/test_load_balancer.py --lb 54.165.62.144

    # Custom number of requests
    python scripts/test_load_balancer.py --lb 54.165.62.144 --requests 12

    # Test a specific endpoint
    python scripts/test_load_balancer.py --lb 54.165.62.144 --endpoint /api/weather

Requirements
------------
    pip install httpx   (already in backend/requirements.txt)
    OR just uses the stdlib urllib if httpx is not installed.
"""

import argparse
import sys
import time
from collections import Counter
from urllib.request import urlopen, Request
from urllib.error import URLError


# ── ANSI colours (disabled on Windows cmd unless ANSI is enabled) ─────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def colour(text: str, col: str) -> str:
    """Apply colour only if stdout is a real terminal."""
    if sys.stdout.isatty():
        return col + text + RESET
    return text


# ── Core test ─────────────────────────────────────────────────────────────────

def run_test(lb_host: str, endpoint: str, n_requests: int) -> None:
    # Ensure the endpoint starts with /
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    base_url = f"http://{lb_host}"
    url      = base_url + endpoint

    print()
    print(colour("━" * 60, CYAN))
    print(colour(f"  Texas ERCOT — Load Balancer Test", BOLD))
    print(colour("━" * 60, CYAN))
    print(f"  Target   : {colour(url, YELLOW)}")
    print(f"  Requests : {colour(str(n_requests), YELLOW)}")
    print(colour("━" * 60, CYAN))
    print()

    backend_hits: Counter = Counter()
    seen_backends: list[str] = []

    for i in range(1, n_requests + 1):
        try:
            req = Request(url, headers={"User-Agent": "LB-Test/1.0"})
            with urlopen(req, timeout=10) as resp:
                served_by = resp.headers.get("X-Served-By", "(header not found)")
                status    = resp.status
        except URLError as exc:
            served_by = f"ERROR — {exc.reason}"
            status    = 0

        backend_hits[served_by] += 1
        seen_backends.append(served_by)

        status_str = colour(str(status), GREEN if status == 200 else RED)
        sb_col     = colour(served_by, YELLOW)
        print(f"  Request {i:>2}  →  [{status_str}]  {sb_col}")

        # Small delay so we don't hammer the connection pool
        if i < n_requests:
            time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print(colour("━" * 60, CYAN))
    print(colour("  Summary", BOLD))
    print(colour("━" * 60, CYAN))

    for backend, count in sorted(backend_hits.items()):
        bar   = "█" * count
        label = colour(backend, YELLOW)
        cnt   = colour(str(count), GREEN)
        print(f"  {label:50s}  {cnt} request(s)  {bar}")

    print()

    # ── Verdict ───────────────────────────────────────────────────────────────
    real_backends = [b for b in backend_hits if "ERROR" not in b and "not found" not in b]

    if len(real_backends) >= 2:
        print(colour("  ✓ PASS  —  Traffic distributed across multiple backends.", GREEN))
        print(f"  Backends observed: {colour(', '.join(sorted(real_backends)), YELLOW)}")
    elif len(real_backends) == 1:
        print(colour("  ⚠ WARN  —  Only one backend was observed.", YELLOW))
        print("  This is normal if the other backend is down, or if the cache")
        print("  returned all responses from the same upstream (sticky caching).")
    else:
        print(colour("  ✗ FAIL  —  Could not reach any backend.", RED))
        print("  Check that the load balancer IP is correct and Nginx is running.")

    print(colour("━" * 60, CYAN))
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Nginx round-robin load balancing by checking X-Served-By headers."
    )
    parser.add_argument(
        "--lb",
        required=True,
        metavar="HOST",
        help="Load balancer IP address or domain name (e.g. 54.165.62.144)",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=6,
        metavar="N",
        help="Number of requests to send (default: 6)",
    )
    parser.add_argument(
        "--endpoint",
        default="/api/forecast?model=prophet&hours=24",
        metavar="PATH",
        help="API endpoint to hit (default: /api/forecast?model=prophet&hours=24)",
    )

    args = parser.parse_args()

    if args.requests < 1:
        parser.error("--requests must be at least 1")

    run_test(
        lb_host   = args.lb,
        endpoint  = args.endpoint,
        n_requests= args.requests,
    )


if __name__ == "__main__":
    main()
