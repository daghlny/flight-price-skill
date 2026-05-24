"""`flight-price doctor` — self-check.

Walks through every layer the CLI depends on and reports green/red per step
with a remediation hint when something's wrong. Use this first when reports
come in that "the CLI stopped working" — most failures are environmental
(missing Chromium, blocked network) and not bugs.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from dataclasses import dataclass
from typing import Callable

from . import __version__


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""


def _c_green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _c_red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 10)
    return Check(
        name="python version",
        ok=ok,
        detail=f"Python {v.major}.{v.minor}.{v.micro}",
        hint="flight-price needs python >= 3.10. Upgrade your interpreter."
             if not ok else "",
    )


def _check_self() -> Check:
    return Check(
        name="flight-price installed",
        ok=True,
        detail=__version__,
    )


def _check_playwright_import() -> Check:
    try:
        import playwright  # noqa: F401
        try:
            from importlib.metadata import version
            ver = version("playwright")
        except Exception:
            ver = "?"
        return Check(name="playwright importable", ok=True, detail=str(ver))
    except ImportError as e:
        return Check(
            name="playwright importable", ok=False,
            detail=str(e),
            hint="reinstall: `pip install playwright`",
        )


def _check_chromium() -> Check:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Check(
            name="chromium binary", ok=False,
            hint="playwright not importable; fix that first",
        )
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            from pathlib import Path
            if path and Path(path).exists():
                return Check(name="chromium binary", ok=True, detail=path)
            return Check(
                name="chromium binary", ok=False,
                detail=f"executable_path={path}",
                hint="run: `playwright install chromium`",
            )
    except Exception as e:
        return Check(
            name="chromium binary", ok=False,
            detail=str(e),
            hint="run: `playwright install chromium`",
        )


def _check_dns() -> Check:
    try:
        addrs = socket.getaddrinfo("tw.trip.com", 443, type=socket.SOCK_STREAM)
        ip = addrs[0][4][0] if addrs else "?"
        return Check(name="DNS for tw.trip.com", ok=True, detail=ip)
    except socket.gaierror as e:
        return Check(
            name="DNS for tw.trip.com", ok=False,
            detail=str(e),
            hint="check your network / DNS resolver",
        )


def _check_http() -> Check:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        "https://tw.trip.com/", method="HEAD",
        headers={"User-Agent": "flight-price-doctor/1.0"},
    )
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=10) as resp:
            dt_ms = int((time.time() - t0) * 1000)
            return Check(
                name="HTTPS to tw.trip.com",
                ok=resp.status < 500,
                detail=f"HTTP {resp.status} in {dt_ms}ms",
                hint="Trip.com may be blocking your IP / region — try a VPN"
                     if resp.status >= 500 else "",
            )
    except (urllib.error.URLError, TimeoutError) as e:
        return Check(
            name="HTTPS to tw.trip.com", ok=False,
            detail=str(e),
            hint="firewall / proxy / DNS issue, or Trip.com is down",
        )


def _check_live_query() -> Check:
    """End-to-end: run a tiny BJS-SHA OW search for tomorrow."""
    from datetime import date, timedelta
    from .trip import query_oneway_range

    target = (date.today() + timedelta(days=14)).isoformat()
    try:
        t0 = time.time()
        results = asyncio.run(
            query_oneway_range(
                "BJS", "SHA", [target],
                concurrency=1, timeout_seconds=30,
            )
        )
        dt_s = time.time() - t0
        if not results:
            return Check(
                name="live query smoke test", ok=False,
                detail="no DayResult returned",
                hint="probably a CLI bug — file an issue",
            )
        r = results[0]
        if r.status != "ok":
            return Check(
                name="live query smoke test", ok=False,
                detail=f"status={r.status}, {r.n_options} options",
                hint="Trip.com responded but no flights parsed — they may "
                     "have changed their response format; file an issue",
            )
        cheapest = r.options[0].price if r.options else "?"
        return Check(
            name="live query smoke test", ok=True,
            detail=f"got {r.n_options} BJS-SHA options for {target} "
                   f"in {dt_s:.1f}s (cheapest: {cheapest} CNY)",
        )
    except Exception as e:
        return Check(
            name="live query smoke test", ok=False,
            detail=f"{type(e).__name__}: {e}",
            hint="depends on previous checks — fix those first",
        )


_CHECKS: list[tuple[str, Callable[[], Check]]] = [
    ("python version",         _check_python),
    ("flight-price installed", _check_self),
    ("playwright importable",  _check_playwright_import),
    ("chromium binary",        _check_chromium),
    ("DNS for tw.trip.com",    _check_dns),
    ("HTTPS to tw.trip.com",   _check_http),
    ("live query smoke test",  _check_live_query),
]


def run(as_json: bool = False) -> int:
    results: list[Check] = []
    if as_json:
        for _name, fn in _CHECKS:
            results.append(fn())
        all_ok = all(c.ok for c in results)
        print(json.dumps(
            {
                "ok": all_ok,
                "checks": [
                    {"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint}
                    for c in results
                ],
            },
            ensure_ascii=False, indent=2,
        ))
        return 0 if all_ok else 1

    n = len(_CHECKS)
    for i, (name, fn) in enumerate(_CHECKS, start=1):
        # Print the check name immediately so the user sees where we are
        # if a slow check (live query, HTTP) hangs.
        print(f"[{i}/{n}] {name}".ljust(40), end="", flush=True)
        c = fn()
        results.append(c)
        mark = _c_green("✓") if c.ok else _c_red("✗")
        print(f" {mark}  {c.detail}")
        if not c.ok and c.hint:
            print(f"         hint: {c.hint}")

    all_ok = all(c.ok for c in results)
    print()
    if all_ok:
        print(_c_green("all checks passed."))
        return 0
    failed = [c.name for c in results if not c.ok]
    print(_c_red(f"failed: {', '.join(failed)}"))
    return 1
