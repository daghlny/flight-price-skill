"""Opt-in file cache for DayResult, keyed by query identity.

The cache stores the **raw query result** (a parsed DayResult, before any
client-side filtering/sorting). On a hit, the caller can still apply
different --airline / --max-stops / --sort / --limit without re-hitting
Trip.com. Filters are NOT part of the cache key for this reason.

Location:  $XDG_CACHE_HOME/flight-price/  (defaults to ~/.cache/flight-price/)
Layout:    one JSON file per query, named by a short hash of the key.
Eviction:  lazy on read — stale entries are deleted as they're noticed.

Cache is OFF by default; --cache opts in. See the man page section
CACHE NOTES for the trade-off rationale.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .trip import (
    DayResult,
    FlightOption,
    Layover,
    ReturnSegment,
    Segment,
)


def _default_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = Path(base) / "flight-price"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(
    origin: str, dest: str, depart: str, ret: str | None,
    cabin: str, adults: int, currency: str,
) -> str:
    raw = f"{origin.upper()}|{dest.upper()}|{depart}|{ret or ''}|{cabin}|{adults}|{currency.upper()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _hydrate_option(d: dict) -> FlightOption:
    return FlightOption(
        price=d["price"],
        currency=d["currency"],
        direct=d["direct"],
        airline=d["airline"],
        flight_nos=d["flight_nos"],
        segments=[Segment(**s) for s in d.get("segments") or []],
        layovers=[Layover(**lv) for lv in d.get("layovers") or []],
        journeys=d.get("journeys") or [],
        return_flights=[ReturnSegment(**r) for r in d.get("return_flights") or []],
        return_stops=d.get("return_stops", 0),
        return_direct=d.get("return_direct", True),
        depart_time=d.get("depart_time", ""),
        total_duration_min=d.get("total_duration_min", 0),
    )


def _hydrate(data: dict) -> DayResult:
    return DayResult(
        date=data["date"],
        return_date=data.get("return_date"),
        options=[_hydrate_option(o) for o in data.get("options") or []],
        n_options=data.get("n_options", 0),
        status=data.get("status", "ok"),
    )


class Cache:
    def __init__(self, ttl_seconds: int = 600, directory: Path | None = None):
        self.ttl = max(0, int(ttl_seconds))
        self.dir = directory or _default_dir()

    def get(
        self, origin: str, dest: str, depart: str, ret: str | None,
        cabin: str, adults: int, currency: str,
    ) -> DayResult | None:
        key = _cache_key(origin, dest, depart, ret, cabin, adults, currency)
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        try:
            return _hydrate(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            # Corrupt cache entry — remove and report a miss.
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def put(
        self, result: DayResult,
        origin: str, dest: str, depart: str, ret: str | None,
        cabin: str, adults: int, currency: str,
    ) -> None:
        # Don't cache transient failures — agents/scripts should be able to
        # retry timeouts on a follow-up call without hitting a stale empty.
        if result.status != "ok":
            return
        key = _cache_key(origin, dest, depart, ret, cabin, adults, currency)
        path = self.dir / f"{key}.json"
        try:
            path.write_text(json.dumps(asdict(result), ensure_ascii=False))
        except OSError:
            pass
