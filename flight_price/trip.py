"""Trip.com flight price data layer.

We query `FlightListSearch` (or its SSE-streamed sibling `FlightListSearchSSE`
for international ODs) for one specific itinerary at a time. This returns the
real, currently-bookable flight inventory — airlines, flight numbers,
direct/transfer, prices — rather than the calendar's misleading "from"
prices.

Scanning a date range therefore requires N page navigations in parallel
(one per date). We share one Chromium process and serialize page creations
under a small concurrency semaphore so trip.com doesn't rate-limit us.

URL pattern: `tw.trip.com/chinaflights/showfarefirst` with full search-form
params reliably triggers the flight list endpoint for every combination
we've tested (domestic/intl × OW/RT).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from playwright.async_api import async_playwright, Browser, Response

FLS_PATH = "/restapi/soa2/27015/FlightListSearch"  # matches both FLS and FlightListSearchSSE

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


@dataclass
class Segment:
    airline: str           # IATA code, e.g. "HX"
    flight_no: str         # "HX305"
    depart_airport: str    # "PEK"
    depart_terminal: str   # "T2"
    depart_time: str       # ISO "2026-06-06 09:20:00"
    arrive_airport: str    # "HKG"
    arrive_terminal: str   # "T1"
    arrive_time: str       # ISO "2026-06-06 13:15:00"
    duration_min: int


@dataclass
class Layover:
    airport: str           # "HKG"
    duration_min: int
    change_terminal: bool  # T1→T2 etc.


@dataclass
class ReturnSegment:
    """Partial return-leg detail decoded from shortPolicyId.

    Trip.com's FlightListSearch does not return per-segment airport/arrive-time
    info for the return leg (those live behind a follow-up FlightDetail call).
    What's available without a second request: flight number, airline code,
    and depart time (UTC ms). We surface those.
    """
    flight_no: str
    airline: str
    depart_time: str       # ISO "2026-06-08 19:55:00" in CST


@dataclass
class FlightOption:
    price: float
    currency: str
    direct: bool                              # outbound direct (no stops)
    airline: str                              # 2-letter code of the first outbound segment
    flight_nos: list[str]                     # OUTBOUND only: ["CA183"] or ["HX305","HX630"]
    segments: list[Segment] = field(default_factory=list)        # outbound full detail
    layovers: list[Layover] = field(default_factory=list)        # outbound layovers
    journeys: list[list[str]] = field(default_factory=list)      # legacy: per-leg flight_nos
    # Return-leg detail (RT only; empty in OW). Decoded from shortPolicyId.
    return_flights: list[ReturnSegment] = field(default_factory=list)
    return_stops: int = 0
    return_direct: bool = True
    # Outbound first-segment depart time, for filtering/sorting (HH:MM in 24h).
    depart_time: str = ""        # ISO "YYYY-MM-DD HH:MM:SS" of first outbound segment
    total_duration_min: int = 0  # outbound total (sum of segments + layovers)


@dataclass
class DayResult:
    date: str
    return_date: str | None
    options: list[FlightOption] = field(default_factory=list)  # ranked by price asc
    n_options: int = 0
    status: str = "ok"   # "ok" | "no_results" | "timeout"


def _bootstrap_url(
    origin: str, dest: str, depart_date: str, return_date: str | None
) -> str:
    """One URL pattern that reliably triggers the flight list endpoint."""
    o, d = origin.upper(), dest.upper()
    # Pick a plausible primary airport per city; backend resolves city codes too.
    _ap = {
        "BJS": "PEK", "SHA": "SHA", "CAN": "CAN", "SZX": "SZX", "CTU": "CTU",
        "TYO": "NRT", "OSA": "KIX", "NYC": "JFK", "LON": "LHR", "SEL": "ICN",
    }
    da = _ap.get(o, o)
    aa = _ap.get(d, d)
    trip = "rt" if return_date else "ow"
    rdate = f"&rdate={return_date}" if return_date else ""
    return (
        "https://tw.trip.com/chinaflights/showfarefirst"
        f"?dcity={o.lower()}&acity={d.lower()}"
        f"&ddate={depart_date}{rdate}"
        f"&dairport={da.lower()}&aairport={aa.lower()}"
        f"&triptype={trip}&class=y"
        "&lowpricesource=searchform&quantity=1"
        "&searchboxarg=t&nonstoponly=off"
        "&locale=zh-TW&curr=CNY"
    )


def _parse_response_body(body: str) -> dict | None:
    """Trip.com returns either a plain JSON object or an SSE stream of
    `data:{...json...}` chunks. Handle both."""
    if not body:
        return None
    if body.lstrip().startswith("{"):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            pass
    chunks = re.findall(
        r"^data:(\{.*?\})$", body, flags=re.MULTILINE | re.DOTALL
    )
    for chunk in reversed(chunks):
        try:
            j = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if j.get("itineraryList"):
            return j
    return None


def _section_point(point: dict | None) -> tuple[str, str]:
    if not point:
        return "", ""
    return point.get("airportCode") or "", point.get("terminal") or ""


def _minutes_between(a: str, b: str) -> int:
    try:
        ta = dt.datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        tb = dt.datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        return int((tb - ta).total_seconds() // 60)
    except Exception:
        return 0


_CST = dt.timezone(dt.timedelta(hours=8))


def _parse_return_from_short_policy_id(spid: str) -> list[ReturnSegment]:
    """Decode return-leg segments from policies[0].shortPolicyId.

    Format of the tail (after the last '^'):
        ROUTES;qty;seg1|seg2|...
    where each seg is `{leg_idx},{seg_idx},?,{flight_no},?,{depart_ts_ms}`.
    leg_idx == 2 → return-leg segment.
    """
    if not spid or "^" not in spid:
        return []
    tail = spid.split("^")[-1]
    parts = tail.split(";")
    if len(parts) < 3:
        return []
    segs_raw = parts[2]
    out: list[ReturnSegment] = []
    for raw in segs_raw.split("|"):
        f = raw.split(",")
        if len(f) < 6:
            continue
        if f[0] != "2":
            continue
        fno = f[3]
        try:
            ts_ms = int(f[5])
        except ValueError:
            continue
        t = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc)
        depart_local = t.astimezone(_CST).strftime("%Y-%m-%d %H:%M:%S")
        # Airline code = leading letters of flight_no (mostly 2 alpha, but a few
        # carriers have digit-letter combos like 3U, 9C — keep first 2 chars).
        airline = fno[:2]
        out.append(ReturnSegment(
            flight_no=fno,
            airline=airline,
            depart_time=depart_local,
        ))
    return out


def _extract_options(payload: dict) -> tuple[list[FlightOption], str]:
    basic = payload.get("basicInfo") or {}
    currency = basic.get("currency") or "CNY"
    options: list[FlightOption] = []
    for it in payload.get("itineraryList") or []:
        policies = it.get("policies") or []
        if not policies:
            continue
        price = policies[0].get("price", {}).get("adult", {}).get("totalPrice")
        if not price or price <= 0:
            continue
        # Trip.com's journeyList only contains the OUTBOUND in RT mode; the
        # return-leg detail is encoded in shortPolicyId (see memory note
        # trip-com-rt-return-leg.md).
        outbound_sections = []
        journeys = it.get("journeyList") or []
        if journeys:
            outbound_sections = journeys[0].get("transSectionList") or []

        segments: list[Segment] = []
        layovers: list[Layover] = []
        airline = ""
        prev_arr_time = None
        prev_arr_airport = None
        prev_arr_terminal = None
        depart_time = ""
        for s in outbound_sections:
            fi = s.get("flightInfo", {}) or {}
            fno = fi.get("flightNo") or ""
            airline_code = fi.get("airlineCode") or ""
            if not fno and not airline_code:
                continue
            if not airline:
                airline = airline_code
            dep_code, dep_term = _section_point(s.get("departPoint"))
            arr_code, arr_term = _section_point(s.get("arrivePoint"))
            dep_time = s.get("departDateTime") or ""
            arr_time = s.get("arriveDateTime") or ""
            if not depart_time:
                depart_time = dep_time
            seg = Segment(
                airline=airline_code,
                flight_no=fno or airline_code,
                depart_airport=dep_code,
                depart_terminal=dep_term,
                depart_time=dep_time,
                arrive_airport=arr_code,
                arrive_terminal=arr_term,
                arrive_time=arr_time,
                duration_min=int(s.get("duration") or 0),
            )
            segments.append(seg)
            if prev_arr_time and prev_arr_airport and dep_time:
                layovers.append(Layover(
                    airport=prev_arr_airport,
                    duration_min=_minutes_between(prev_arr_time, dep_time),
                    change_terminal=(
                        bool(prev_arr_terminal) and bool(dep_term)
                        and prev_arr_terminal != dep_term
                    ),
                ))
            prev_arr_time = arr_time
            prev_arr_airport = arr_code
            prev_arr_terminal = arr_term

        outbound_nos = [s.flight_no for s in segments]
        outbound_stops = max(0, len(segments) - 1)
        direct = outbound_stops == 0

        spid = policies[0].get("shortPolicyId") or ""
        return_flights = _parse_return_from_short_policy_id(spid)
        return_stops = max(0, len(return_flights) - 1)

        # Outbound total duration: from first segment's depart to last segment's arrive
        total_duration = 0
        if segments:
            total_duration = _minutes_between(
                segments[0].depart_time, segments[-1].arrive_time
            )

        options.append(
            FlightOption(
                price=float(price),
                currency=currency,
                direct=direct,
                airline=airline,
                flight_nos=outbound_nos,
                segments=segments,
                layovers=layovers,
                journeys=[outbound_nos] + (
                    [[r.flight_no for r in return_flights]] if return_flights else []
                ),
                return_flights=return_flights,
                return_stops=return_stops,
                return_direct=(return_stops == 0) if return_flights else True,
                depart_time=depart_time,
                total_duration_min=total_duration,
            )
        )
    return options, currency


async def _query_one(
    browser: Browser,
    origin: str,
    dest: str,
    depart_date: str,
    return_date: str | None,
    *,
    timeout_seconds: int = 30,
) -> DayResult:
    captured: dict[str, str] = {}
    done = asyncio.Event()

    async def on_response(response: Response) -> None:
        if FLS_PATH not in response.url:
            return
        try:
            body = await response.text()
        except Exception:
            return
        if not body:
            return
        kind = "sse" if "SSE" in response.url else "json"
        captured[kind] = body
        # Either kind being present is enough; SSE may take longer, plain JSON
        # is usually the one we get on domestic.
        done.set()

    ctx = await browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=UA,
        viewport={"width": 1440, "height": 900},
    )
    timed_out = False
    try:
        page = await ctx.new_page()
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        url = _bootstrap_url(origin, dest, depart_date, return_date)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            pass
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
        # If we got SSE earlier but it was empty, wait a touch longer for the
        # full chunk to land (trip.com sometimes flushes empty heartbeats first).
        if captured:
            await asyncio.sleep(1.5)
    finally:
        await ctx.close()

    body = captured.get("sse") or captured.get("json")
    payload = _parse_response_body(body) if body else None
    if not payload:
        # Distinguish: nothing arrived at all (timeout) vs response arrived
        # but couldn't be parsed (treat as no_results so agent doesn't retry).
        status = "timeout" if (timed_out and not captured) else "no_results"
        return DayResult(
            date=depart_date,
            return_date=return_date,
            options=[],
            n_options=0,
            status=status,
        )
    options, _ = _extract_options(payload)
    options.sort(key=lambda o: o.price)
    return DayResult(
        date=depart_date,
        return_date=return_date,
        options=options,
        n_options=len(options),
        status="ok" if options else "no_results",
    )


async def query_pairs(
    origin: str,
    dest: str,
    pairs: Sequence[tuple[str, str | None]],
    *,
    headless: bool = True,
    concurrency: int = 3,
    timeout_seconds: int = 30,
) -> list[DayResult]:
    """Scan an arbitrary list of (outbound_date, return_date_or_None) pairs.

    The OW/RT helpers below are thin wrappers around this. Use this directly
    when the date pairs don't form a contiguous range (e.g. exploring multiple
    holiday-shifted RT combinations in one call).
    """
    results: list[DayResult] = [None] * len(pairs)  # type: ignore
    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            async def worker(idx: int, dep: str, ret: str | None) -> None:
                async with sem:
                    results[idx] = await _query_one(
                        browser, origin, dest, dep, ret,
                        timeout_seconds=timeout_seconds,
                    )

            await asyncio.gather(
                *[worker(i, dep, ret) for i, (dep, ret) in enumerate(pairs)]
            )
        finally:
            await browser.close()

    return [r for r in results if r is not None]


async def query_oneway_range(
    origin: str,
    dest: str,
    dates: Sequence[str],
    *,
    headless: bool = True,
    concurrency: int = 3,
    timeout_seconds: int = 30,
) -> list[DayResult]:
    pairs: list[tuple[str, str | None]] = [(d, None) for d in dates]
    return await query_pairs(
        origin, dest, pairs,
        headless=headless,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
    )


async def query_roundtrip_range(
    origin: str,
    dest: str,
    departures: Sequence[str],
    *,
    stay_nights: int | None = None,
    return_date: str | None = None,
    headless: bool = True,
    concurrency: int = 3,
    timeout_seconds: int = 30,
) -> list[DayResult]:
    """One of `stay_nights` or `return_date` must be provided."""
    if stay_nights is None and return_date is None:
        raise ValueError("provide stay_nights or return_date")
    pairs: list[tuple[str, str | None]] = []
    for dep in departures:
        if return_date is not None:
            pairs.append((dep, return_date))
        else:
            ret = (dt.date.fromisoformat(dep) + dt.timedelta(days=stay_nights)).isoformat()
            pairs.append((dep, ret))
    return await query_pairs(
        origin, dest, pairs,
        headless=headless,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
    )
